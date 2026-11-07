from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from pathlib import Path

from . import catalog

_INSTALLED = False
_LOCK = threading.Lock()
MANAGED_METADATA = {'filename', 'source', 'revision', 'local_path', 'family', 'tasks', 'weights_size_bytes', 'url', 'description'}
SYSTEM_TAGS = {'models', 'speech-catalog', 'uploaded', 'missing', 'preview'}


def merge_annotations(records, *, previous=None, managed=True):
    """Keep all manual tags/values, including conflicting values from duplicate cards."""
    if previous is not None and not managed:
        records = [(previous['tags'], previous['metadata']), *records]
    tags, values = set(), {}
    def add(key, value):
        bucket = values.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)
    for record_tags, metadata in records:
        tags.update(tag for tag in record_tags if tag not in SYSTEM_TAGS and not tag.startswith('model_type:'))
        for key, value in metadata.items():
            if key not in MANAGED_METADATA and key != '_speech_catalog_conflicts':
                add(key, value)
        for key, variants in metadata.get('_speech_catalog_conflicts', {}).items():
            for value in variants:
                add(key, value)
    result = {key: variants[0] for key, variants in values.items()}
    conflicts = {key: variants for key, variants in values.items() if len(variants) > 1}
    if conflicts:
        result['_speech_catalog_conflicts'] = conflicts
    return {'tags': sorted(tags), 'metadata': result}


def catalog_payload():
    return {'category': catalog.CATEGORY, 'models': [
        {'id': model.id, 'display_name': model.display_name, 'family': model.family,
         'revision': model.revision, 'local_path': str(model.path),
         'filename': model.descriptor_name, 'metadata': catalog.metadata(model)}
        for model in catalog.load_models()]}


def register_assets():
    """Register small bundle descriptions in the native asset library, in place."""
    from app.assets.services import get_asset_detail, register_file_in_place, update_asset_metadata, delete_asset_reference
    from app.assets.database.models import Asset, AssetContent
    from app.database.db import create_session
    from sqlalchemy import select

    def owned_references():
        # The native scanner may have registered these same files already.
        # Adopt its records rather than creating a second card for each model.
        with create_session() as session:
            rows = session.execute(select(Asset.id, AssetContent.path).join(AssetContent, Asset.content_id == AssetContent.id)
                .where(AssetContent.path.startswith(str(catalog.CATALOG_ROOT.resolve()) + os.sep, autoescape=True))).all()
        root = catalog.CATALOG_ROOT.resolve()
        return [(reference_id, Path(path)) for reference_id, path in rows if path
                and Path(path).parent.resolve() == root
                and re.fullmatch(r'[A-Za-z0-9_.-]+--[a-f0-9]{12}\.json', Path(path).name)]

    with _LOCK:
        models = catalog.load_models()
        existing = owned_references()
        snapshots = [(reference_id, path, get_asset_detail(reference_id)) for reference_id, path in existing]
        annotations_path = catalog.CATALOG_ROOT.parent / 'model-annotations.json'
        annotations = json.loads(annotations_path.read_text(encoding='utf-8')) if annotations_path.exists() else {'schema_version': 1, 'models': {}}
        if annotations.get('schema_version') != 1 or not isinstance(annotations.get('models'), dict):
            raise ValueError('Неизвестный формат аннотаций моделей')
        grouped, managed = {}, set()
        for reference_id, path, detail in snapshots:
            if detail is None:
                continue
            metadata = detail.ref.user_metadata or {}
            try:
                descriptor = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                descriptor = {}
            source = metadata.get('source') or descriptor.get('source')
            revision = metadata.get('revision') or descriptor.get('revision', '')
            identity = f'{source}@{revision}' if source else 'orphan:' + path.name
            grouped.setdefault(identity, []).append((detail.tags, metadata))
            if 'speech-catalog' in detail.tags or metadata.get('source'):
                managed.add(identity)
        for identity, records in grouped.items():
            annotations['models'][identity] = merge_annotations(records,
                previous=annotations['models'].get(identity), managed=identity in managed)
        descriptors = catalog.write_descriptors(models)
        registered = {}
        for model, path in zip(models, descriptors):
            key = model.descriptor_name
            details = [detail for reference_id, location, detail in snapshots if location == path
                       and detail is not None
                       and detail.asset is not None and not detail.asset.is_missing]
            details.sort(key=lambda detail: 'uploaded' in detail.tags)
            detail = details[0] if details else None
            if detail is None:
                result = register_file_in_place(str(path), model.display_name,
                    ['models', 'model_type:' + catalog.CATEGORY, 'speech-catalog'], 'application/json', content_written=False)
                reference_id = result.ref.id
            else:
                reference_id = detail.ref.id
            identity = f'{model.id}@{model.revision}'
            saved = annotations['models'].get(identity, annotations['models'].get('orphan:' + path.name, {'tags': [], 'metadata': {}}))
            user_metadata = dict(saved['metadata'])
            user_metadata.update({
                'filename': model.descriptor_name, 'source': model.id, 'revision': model.revision,
                'local_path': str(model.path), 'family': model.family,
                'tasks': model.manifest.get('tasks', []), 'weights_size_bytes': model.descriptor['weights_size_bytes'],
                'url': 'https://huggingface.co/' + model.id,
                'description': 'Комплект локальной модели; эта запись указывает на каталог, а не на отдельный файл весов'})
            tags = sorted(set(saved['tags']) | {'models', 'model_type:' + catalog.CATEGORY, 'speech-catalog'})
            update_asset_metadata(reference_id, name=model.display_name, tags=tags, user_metadata=user_metadata)
            registered[key] = reference_id
        # Persist annotations before removing any old card, also when its model
        # is temporarily absent. The key is HF identity/revision, never a DB UUID.
        temporary = annotations_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(annotations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(annotations_path)
        for reference_id, path in owned_references():
            if reference_id not in registered.values():
                delete_asset_reference(reference_id)
        return {'registered': len(registered)}


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    import folder_paths
    from server import PromptServer
    server = getattr(PromptServer, 'instance', None)
    if server is None:
        return
    from aiohttp import web
    from .results import install_routes
    catalog.write_descriptors(catalog.load_models())
    folder_paths.add_model_folder_path(catalog.CATEGORY, str(catalog.CATALOG_ROOT))
    paths, extensions = folder_paths.folder_names_and_paths[catalog.CATEGORY]
    folder_paths.folder_names_and_paths[catalog.CATEGORY] = (paths, extensions | {'.json'})
    install_routes(server.routes)

    @server.routes.get('/speech/catalog')
    async def get_catalog(request):
        return web.json_response(catalog_payload())

    @server.routes.post('/speech/catalog/register')
    async def register_catalog(request):
        try:
            return web.json_response(await asyncio.to_thread(register_assets))
        except Exception as error:
            logging.warning('Speech model library registration: %s', type(error).__name__)
            return web.json_response({'error': 'Не удалось зарегистрировать записи библиотеки моделей'}, status=503)

    async def on_startup(application):
        try:
            await asyncio.to_thread(register_assets)
        except Exception as error:
            logging.warning('Speech model library startup: %s', type(error).__name__)

    server.app.on_startup.append(on_startup)
    _INSTALLED = True
