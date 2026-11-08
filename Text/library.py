import asyncio
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from .config import load_config


CATEGORY = 'Text'
TAGS = {'models', 'model_type:Text', 'text-catalog'}
ENTRY_PATTERN = re.compile(r'text-[0-9a-f]{24}\.json')
MANIFEST_FIELDS = ('source', 'revision', 'format', 'family')
SYSTEM_TAGS = TAGS | {'uploaded', 'missing', 'preview'}
MANAGED_METADATA = {*MANIFEST_FIELDS, 'filename', 'description', 'model_id', 'profiles', 'weights_size_bytes',
                    'url', 'modelspec.title', 'modelspec.description', 'modelspec.author', 'modelspec.architecture'}
CONFLICTS = '_text_catalog_conflicts'


def model_details(path):
    if path is None:
        return {}
    try:
        manifest = json.loads(path.read_text(encoding='utf-8-sig'))
        details = {key: value.strip() for key in MANIFEST_FIELDS
                   if isinstance(value := manifest.get(key), str) and value.strip()}
        weights = manifest.get('weight_files') or [manifest.get('weight_file')]
        if isinstance(weights, list) and all(isinstance(item, dict) and type(item.get('size_bytes')) is int
                                             and item['size_bytes'] > 0 for item in weights):
            details['weights_size_bytes'] = sum(item['size_bytes'] for item in weights)
        return details
    except (OSError, ValueError, AttributeError) as error:
        logging.warning('Text: не удалось прочитать манифест %s (%s)', path, type(error).__name__)
        return {}


def model_entries(config):
    entries = []
    for model_id, name in config.models.items():
        profiles = {server.name: server.models[name] for server in config.servers.values() if name in server.models}
        if profiles:
            digest = sha256(model_id.encode('utf-8')).hexdigest()[:24]
            entries.append({'id': model_id, 'name': name, 'profiles': profiles,
                            'filename': f'text-{digest}.json',
                            'details': model_details(config.manifests.get(model_id))})
    return entries


def metadata(entry):
    profiles = ', '.join(entry['profiles'])
    detail = entry.get('details', {})
    summary = [('Сервер' if len(entry['profiles']) == 1 else 'Серверы') + f': {profiles}']
    if detail.get('format'):
        summary.append(detail['format'])
    if detail.get('source'):
        summary.append(detail['source'])
    description = ' · '.join(summary)
    source = detail.get('source', '')
    link = ({'url': f'https://huggingface.co/{source}', 'modelspec.author': source.split('/')[0]}
            if re.fullmatch(r'[\w.-]+/[\w.-]+', source, flags=re.ASCII) else {})
    architecture = {'modelspec.architecture': detail['family']} if detail.get('family') else {}
    return {
        'filename': entry['filename'],
        'modelspec.title': entry['name'],
        'description': description,
        'modelspec.description': description,
        'model_id': entry['id'],
        'profiles': entry['profiles'],
        **detail,
        **link,
        **architecture,
    }


def merge_annotations(details, previous=None):
    tags, values = set(), {}
    records = [(detail.tags, detail.ref.user_metadata or {}) for detail in details]
    if previous is not None:
        records.insert(0, (previous['tags'], previous['metadata']))
    for record_tags, fields in records:
        tags.update(tag for tag in record_tags if tag not in SYSTEM_TAGS and not tag.startswith('model_type:'))
        for key, value in fields.items():
            if key in MANAGED_METADATA or key == CONFLICTS:
                continue
            variants = values.setdefault(key, [])
            for candidate in (value, *fields.get(CONFLICTS, {}).get(key, ())):
                if candidate not in variants:
                    variants.append(candidate)
    merged = {key: variants[0] for key, variants in values.items()}
    conflicts = {key: variants for key, variants in values.items() if len(variants) > 1}
    if conflicts:
        merged[CONFLICTS] = conflicts
    return {'tags': sorted(tags), 'metadata': merged}


def write_annotations(path, annotations):
    stream = NamedTemporaryFile('w', dir=path.parent, encoding='utf-8', suffix='.tmp', delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(annotations, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def register_assets(config, entries):
    from app.assets.database.models import Asset, AssetContent
    from app.assets.services import delete_asset_reference, get_asset_detail, register_file_in_place, update_asset_metadata
    from app.database.db import create_session
    from sqlalchemy import select

    root = config.catalog.resolve()
    if entries:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        return
    annotations_path = root.parent / f'{root.name or "text-model-catalog"}-annotations.json'
    original = json.loads(annotations_path.read_text(encoding='utf-8')) if annotations_path.exists() else {}
    annotations = original.copy()
    with create_session() as session:
        rows = session.execute(
            select(Asset.id, AssetContent.path).join(AssetContent, Asset.content_id == AssetContent.id)
            .where(AssetContent.path.startswith(str(root) + os.sep, autoescape=True))
        ).all()
    existing = {}
    for ref_id, locator in rows:
        path = Path(locator)
        if path.parent == root and ENTRY_PATTERN.fullmatch(path.name):
            detail = get_asset_detail(ref_id)
            if detail is not None and detail.asset is not None and not detail.asset.is_missing:
                existing.setdefault(path, []).append(detail)

    desired = set()
    for entry in entries:
        path = root / entry['filename']
        payload = json.dumps({'type': 'text-model-entry', 'id': entry['id']}, ensure_ascii=False) + '\n'
        if path.is_symlink():
            raise ValueError('Запись каталога Text должна быть обычным файлом')
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(payload)
        except FileExistsError:
            if path.read_text(encoding='utf-8') != payload:
                raise ValueError('Файл каталога Text уже используется')
        desired.add(path)

        details = sorted(existing.get(path, ()), key=lambda detail: 'text-catalog' not in detail.tags)
        saved = annotations.pop(path.name, None)
        # A current managed card reflects manual edits, including explicit removals.
        user = merge_annotations(details, saved if not details or 'text-catalog' not in details[0].tags else None)
        if details:
            ref_id = details[0].ref.id
        else:
            ref_id = register_file_in_place(
                str(path), entry['name'], sorted(TAGS), 'application/json', content_written=False,
            ).ref.id
        update_asset_metadata(ref_id, name=entry['name'], tags=sorted(set(user['tags']) | TAGS),
                              user_metadata={**user['metadata'], **metadata(entry)})
        for duplicate in details[1:]:
            if 'text-catalog' in duplicate.tags:
                delete_asset_reference(duplicate.ref.id)

    retired = []
    for path, details in existing.items():
        managed = [detail for detail in details if 'text-catalog' in detail.tags]
        if path not in desired and managed:
            user = merge_annotations(details)
            if user['tags'] or user['metadata']:
                annotations[path.name] = user
            else:
                annotations.pop(path.name, None)
            retired.extend(managed)
    if annotations != original:
        write_annotations(annotations_path, annotations)
    for detail in retired:
        delete_asset_reference(detail.ref.id)
    for path in root.glob('text-*.json'):
        if path in desired or path.is_symlink() or not ENTRY_PATTERN.fullmatch(path.name):
            continue
        try:
            content = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(content, dict) and content.get('type') == 'text-model-entry':
            path.unlink(missing_ok=True)


def install():
    import folder_paths
    from aiohttp import web
    from server import PromptServer

    server = getattr(PromptServer, 'instance', None)
    if server is None:
        return
    config = load_config()
    folder_paths.add_model_folder_path(CATEGORY, str(config.catalog))
    paths, extensions = folder_paths.folder_names_and_paths[CATEGORY]
    folder_paths.folder_names_and_paths[CATEGORY] = (paths, extensions | {'.json'})

    @server.routes.get('/text/models')
    async def get_models(_request):
        return web.json_response([
            {'filename': entry['filename'], 'metadata': metadata(entry)}
            for entry in model_entries(load_config())
        ])

    async def on_startup(_application):
        try:
            await asyncio.to_thread(register_assets, config, model_entries(config))
        except Exception as error:
            logging.warning('Text model library startup: %s', type(error).__name__)

    server.app.on_startup.append(on_startup)
