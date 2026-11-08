import asyncio
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re

from .config import load_config


CATEGORY = 'Text'
TAGS = {'models', 'model_type:Text', 'text-catalog'}
ENTRY_PATTERN = re.compile(r'text-[0-9a-f]{24}\.json')


def model_entries(config):
    entries = []
    for model_id, name in config.models.items():
        profiles = {server.name: server.models[name] for server in config.servers.values() if name in server.models}
        if profiles:
            digest = sha256(model_id.encode('utf-8')).hexdigest()[:24]
            entries.append({'id': model_id, 'name': name, 'profiles': profiles,
                            'filename': f'text-{digest}.json'})
    return entries


def metadata(entry):
    profiles = ', '.join(entry['profiles'])
    return {
        'filename': entry['filename'],
        'modelspec.title': entry['name'],
        'description': f'Текстовая модель из конфигурации пакета Text. Профили: {profiles}.',
        'model_id': entry['id'],
        'profiles': entry['profiles'],
    }


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
        if details:
            detail = details[0]
            ref_id = detail.ref.id
            user_metadata = dict(detail.ref.user_metadata or {})
            tags = set(detail.tags)
        else:
            ref_id = register_file_in_place(
                str(path), entry['name'], sorted(TAGS), 'application/json', content_written=False,
            ).ref.id
            user_metadata = {}
            tags = set()
        user_metadata.update(metadata(entry))
        update_asset_metadata(ref_id, name=entry['name'], tags=sorted(tags | TAGS), user_metadata=user_metadata)
        for duplicate in details[1:]:
            if 'text-catalog' in duplicate.tags:
                delete_asset_reference(duplicate.ref.id)

    for path, details in existing.items():
        if path not in desired:
            for detail in details:
                if 'text-catalog' in detail.tags:
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
