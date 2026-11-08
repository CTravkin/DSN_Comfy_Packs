from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULTS = {
    'device': None,
    'models': {'whisper': None, 'diarization': None, 'voiceprint': None},
    'file_setup': {'include_datetime': True},
    'whisper': {
        'language': None, 'task': 'transcribe', 'beam_size': 5, 'vad': True, 'compute_type': None,
        'export': {'txt': False, 'json': False, 'srt': False, 'vtt': False},
    },
    'diarization': {'speakers': None, 'min_speakers': 0, 'max_speakers': 0, 'export': {'rttm': False}},
    'voiceprint': {'start': 0.0, 'end': None},
    'voiceprint_identity': {'threshold': 0.25, 'minimum_margin': 0.05},
    'speaker_transcript': {'max_gap': 1.0, 'export': {'txt': True, 'json': True}},
}
RUNTIMES = {
    'whisper': {'python': None, 'runner': None},
    'speaker_analysis': {'python': None, 'runners': {'diarization': None, 'voiceprint': None, 'transcript': None}},
    'timeout': 3600,
    'offline': True,
}


@dataclass(frozen=True)
class Config:
    root: Path
    paths: dict[str, Path | None]
    model_roots: tuple[Path, ...]
    runtime: dict
    defaults: dict

    def resolve(self, value: str) -> Path:
        return (self.root / Path(value).expanduser()).resolve()


def config_locations():
    try:
        import folder_paths
    except ModuleNotFoundError as error:
        if error.name != 'folder_paths':
            raise
        base = Path.home() / '.dsn-comfy' / 'speech'
        return base, base, base / 'Models'
    base = Path(folder_paths.base_path)
    return base, Path(folder_paths.get_user_directory()), Path(folder_paths.models_dir) / 'speech'


def merge_settings(values: dict, defaults: dict, prefix: str) -> dict:
    if not isinstance(values, dict):
        raise ValueError(f'{prefix} должен быть объектом')
    result = {}
    for key, fallback in defaults.items():
        value = values.get(key, fallback)
        field = f'{prefix}.{key}'
        if isinstance(fallback, dict):
            value = merge_settings(value, fallback, field)
        elif fallback is not None:
            valid_types = (int, float) if type(fallback) is float else (type(fallback),)
            if type(value) not in valid_types:
                raise ValueError(f'Некорректное значение {field}')
        result[key] = value
    return result


def validate_defaults(defaults):
    language = defaults['whisper']['language']
    if language not in (None, 'auto') and (not isinstance(language, str) or not re.fullmatch('[a-z]{2}', language)):
        raise ValueError('defaults.whisper.language должен быть null, auto или кодом ISO 639-1')
    if defaults['device'] not in (None, 'auto', 'cpu', 'cuda'):
        raise ValueError('defaults.device должен быть null, auto, cpu или cuda')
    if defaults['whisper']['task'] not in ('transcribe', 'translate'):
        raise ValueError('defaults.whisper.task должен быть transcribe или translate')
    if any(value is not None and (not isinstance(value, str) or not value.strip())
           for value in (*defaults['models'].values(), defaults['whisper']['compute_type'])):
        raise ValueError('Укажите непустое название модели или типа вычислений')
    diarization = defaults['diarization']
    speakers = 0 if diarization['speakers'] is None else diarization['speakers']
    if any(type(value) is not int or not 0 <= value <= 100
           for value in (speakers, diarization['min_speakers'], diarization['max_speakers'])):
        raise ValueError('Число спикеров должно быть целым числом от 0 до 100')
    if not 1 <= defaults['whisper']['beam_size'] <= 50:
        raise ValueError('defaults.whisper.beam_size должен быть от 1 до 50')
    voiceprint = defaults['voiceprint']
    end = 0 if voiceprint['end'] is None else voiceprint['end']
    if not voiceprint['start'] >= 0 or type(end) not in (int, float) or not end >= 0 or (end and end <= voiceprint['start']):
        raise ValueError('Проверьте границы фрагмента в defaults.voiceprint')
    identity = defaults['voiceprint_identity']
    if not (-1 <= identity['threshold'] <= 1 and 0 <= identity['minimum_margin'] <= 2
            and 0 <= defaults['speaker_transcript']['max_gap'] <= 60):
        raise ValueError('Проверьте пороги идентификации и длительность паузы')


def load_config(path: Path | None = None) -> Config:
    base, user, models = config_locations()
    configured = os.environ.get('COMFY_SPEECH_CONFIG')
    explicit = path is not None or bool(configured)
    path = (path or (Path(configured) if configured else user / 'package-configs' / 'speech-config.json')).expanduser().resolve()
    try:
        exists = path.is_file()
        data = json.loads(path.read_text(encoding='utf-8-sig')) if explicit or exists else {}
        if not isinstance(data, dict):
            raise ValueError('Конфигурация должна быть объектом')
        legacy_root = os.environ.get('SPEECH_AI_ROOT') if not (explicit or exists) else None
        root_value = data.get('root')
        root = (path.parent / Path(root_value).expanduser()).resolve() if root_value else base.resolve()
        if legacy_root:
            root = Path(legacy_root).expanduser().resolve()
        overrides = data.get('paths', {})
        if not isinstance(overrides, dict):
            raise ValueError('Раздел paths должен быть объектом')
        paths = {}
        for key, fallback in (
            ('model_catalog', root / 'Data/Comfy/model-catalog' if legacy_root else path.parent / 'speech-model-catalog'),
            ('voiceprints', Path('Data/Speakers')),
            ('huggingface_cache', Path('Cache/HuggingFace/Hub')),
            ('speechbrain_cache', Path('Cache/SpeechBrain')),
            ('temp', Path('Temp/Speech')),
            ('ffmpeg_bin', None),
        ):
            value = overrides.get(key) or fallback
            paths[key] = (root / Path(value).expanduser()).resolve() if value is not None else None
        paths['model_annotations'] = (root / Path(overrides.get('model_annotations')
                                      or paths['model_catalog'].parent / 'model-annotations.json').expanduser()).resolve()
        model_roots = overrides.get('model_roots')
        if model_roots is None:
            model_roots = [root / 'Models' if legacy_root else models]
        elif not isinstance(model_roots, list) or not all(isinstance(value, str) and value.strip() for value in model_roots):
            raise ValueError('paths.model_roots должен быть списком путей')
        runtime = merge_settings(data.get('runtime', {}), RUNTIMES, 'runtime')
        if runtime['timeout'] <= 0:
            raise ValueError('runtime.timeout должен быть больше нуля')
        defaults = merge_settings(data.get('defaults', {}), DEFAULTS, 'defaults')
        validate_defaults(defaults)
        return Config(root, paths, tuple((root / Path(value).expanduser()).resolve() for value in model_roots),
                      runtime, defaults)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise ValueError(f'Не удалось прочитать конфигурацию Speech ({path}): {error}') from None
