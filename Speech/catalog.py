from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import load_config

CONFIG = load_config()
AI_ROOT = CONFIG.root
CATALOG_ROOT = CONFIG.paths['model_catalog']
CATEGORY = 'Speech'


@dataclass(frozen=True)
class Model:
    id: str
    display_name: str
    family: str
    path: Path
    revision: str
    manifest: dict

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}', self.id):
            raise ValueError('Идентификатор модели должен иметь вид owner/repository Hugging Face')

    @property
    def descriptor_name(self):
        identity = json.dumps(self.descriptor, ensure_ascii=False, sort_keys=True)
        return self.id.replace('/', '--') + '--' + hashlib.sha256(identity.encode()).hexdigest()[:12] + '.json'

    @property
    def descriptor(self):
        weights = self.manifest.get('weight_files') or [self.manifest.get('weight_file', {})]
        return {'type': 'model-catalog-entry', 'source': self.id, 'display_name': self.display_name,
                'revision': self.revision, 'local_path': str(self.path.resolve()), 'family': self.family,
                'url': 'https://huggingface.co/' + self.id, 'format': self.manifest.get('format'),
                'license': self.manifest.get('license'), 'tasks': self.manifest.get('tasks', []),
                'weights_size_bytes': sum(entry.get('size_bytes', 0) for entry in weights)}


def load_models(root: Path | None = None) -> list[Model]:
    models = []
    roots = (root,) if root is not None else CONFIG.model_roots
    manifests = {manifest.resolve() for directory in roots for manifest in directory.rglob('manifest.json')
                 if 'Comfy' not in manifest.relative_to(directory).parts}
    for manifest_path in sorted(manifests):
        payload = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        source = payload.get('source', '')
        family = payload.get('family') or ('whisper' if payload.get('format', '').startswith('CTranslate2') else None)
        if family not in {'whisper', 'diarization', 'voiceprint'}:
            continue
        weights = payload.get('weight_files') or [payload.get('weight_file', {})]
        folder = manifest_path.parent.resolve()
        paths = [(folder / entry.get('path', '')).resolve() for entry in weights]
        if not paths or not all(path.is_relative_to(folder) and path.is_file() for path in paths):
            continue
        name = payload.get('display_name') or payload.get('name')
        if not name:
            continue
        if any(model.display_name == name for model in models):
            raise ValueError(f'Повтор идентификатора или названия модели: {source}')
        models.append(Model(source, name, family, folder, payload.get('revision', ''), payload))
    return models


def select_model(value: str, family: str, root: Path | None = None) -> Model:
    matches = [model for model in load_models(root) if model.family == family and value in (model.id, model.display_name)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError('Установлено несколько ревизий; требуется видимое название модели')
    raise ValueError(f'Совместимая локальная модель не найдена: {value}')


def write_descriptors(models: list[Model], destination: Path = CATALOG_ROOT) -> list[Path]:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    paths = []
    for model in models:
        path = destination / model.descriptor_name
        if path.resolve().parent != destination or path.is_symlink():
            raise ValueError('Запись каталога выходит за пределы своего каталога')
        text = json.dumps(model.descriptor, ensure_ascii=False, indent=2) + '\n'
        if not path.exists() or path.read_text(encoding='utf-8') != text:
            path.write_text(text, encoding='utf-8')
        paths.append(path)
    for stale in destination.glob('*.json'):
        if stale in paths or stale.is_symlink() or stale.resolve().parent != destination:
            continue
        try:
            owned = json.loads(stale.read_text(encoding='utf-8')).get('type') == 'model-catalog-entry'
        except (ValueError, OSError):
            owned = False
        if owned:
            stale.unlink()
    return paths


def metadata(model: Model) -> dict:
    return {'modelspec.title': model.display_name, 'modelspec.author': model.id.split('/')[0],
            'modelspec.description': f'{model.id}\n{model.path}\nRevision: {model.revision}\nhttps://huggingface.co/{model.id}',
            'modelspec.architecture': model.family, 'modelspec.tags': 'speech,local,' + model.family,
            'source': model.id, 'revision': model.revision, 'local_path': str(model.path)}
