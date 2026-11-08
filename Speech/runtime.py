from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .catalog import AI_ROOT, Model
from .files import Files, publish, read_path, sha256

RUNNERS = Path(__file__).resolve().with_name('runners')
WHISPER_RUNNER = RUNNERS / 'whisper_tool.py'
RUNNER_SCRIPTS = {
    'Whisper': WHISPER_RUNNER,
    'SpeakerDiarization': RUNNERS / 'speaker_diarization.py',
    'Voiceprint': RUNNERS / 'voiceprint_tool.py',
    'SpeakerTranscript': RUNNERS / 'speaker_transcript.py',
}


def run_cli(python: Path, cli: Path, arguments: list[str], *, ai_root: Path = AI_ROOT,
            timeout: float = 3600, cancel_check: Callable | None = None) -> dict:
    for path in (python, cli):
        if not path.is_file():
            raise FileNotFoundError(f'Локальный компонент не найден: {path}')
    environment = os.environ.copy()
    site = python.parent.parent / 'Lib' / 'site-packages'
    libraries = [site / 'nvidia' / part / 'bin' for part in ('cublas', 'cudnn')]
    temp = ai_root / 'Temp' / 'Speech'
    temp.mkdir(parents=True, exist_ok=True)
    environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                       PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                       HF_HUB_CACHE=str(ai_root / 'Cache' / 'HuggingFace' / 'Hub'),
                       TEMP=str(temp), TMP=str(temp),
                       PATH=os.pathsep.join([*(str(p) for p in libraries), environment.get('PATH', '')]))
    process = subprocess.Popen([str(python), '-B', str(cli), *arguments], shell=False,
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8', errors='replace',
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    started = time.monotonic()
    try:
        while True:
            if cancel_check:
                cancel_check()
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError('Превышено время обработки')
            try:
                stdout, _ = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f'Локальный инструмент завершился с кодом {process.returncode}: {cli.parent.name}')
    return json.loads(stdout)


def invoke(tool: str, arguments: list[str], *, ai_root: Path = AI_ROOT, cancel_check=None):
    environment = 'FasterWhisper' if tool == 'Whisper' else 'SpeakerAnalysis'
    venv = ai_root / 'Apps' / environment / '.venv'
    python = venv / 'Scripts' / 'python.exe' if os.name == 'nt' else venv / 'bin' / 'python'
    return run_cli(python, RUNNER_SCRIPTS[tool], arguments, ai_root=ai_root, cancel_check=cancel_check)


def voiceprint_cache(model: Model, ai_root: Path) -> Path:
    key = hashlib.sha256(f'{model.id}|{model.revision}|{model.path.resolve()}'.encode()).hexdigest()[:20]
    return ai_root / 'Cache' / 'SpeechBrain' / key


def read_result(path: Path, context: Files | None = None, *, model: Model | None = None) -> dict:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if context:
        context.validate_source()
        if read_path(data.get('source', '')) != context.source or data.get('source_sha256') != context.source_sha256:
            raise ValueError('Результат относится к другому исходному файлу или не содержит проверенного хеша')
    if model and read_path(str(data.get('model', ''))) != model.path.resolve():
        raise ValueError('Результат создан другой моделью')
    return data


def reuse(existing_json: str, context: Files, model: Model, stage: Path, *, root: Path = AI_ROOT) -> Path | None:
    if not existing_json.strip():
        return None
    source = read_path(existing_json, root)
    read_result(source, context, model=model)
    target = stage / source.name
    shutil.copyfile(source, target)
    return target


def whisper(context: Files, model: Model, *, language='auto', task='transcribe', prompt='',
            device='cuda', save_json=False, save_text=False, srt=False, vtt=False,
            existing_json='', beam_size=5, vad=True, ai_root=AI_ROOT, cancel_check=None):
    if task not in model.manifest.get('tasks', ['transcribe']):
        raise ValueError('Выбранная модель не поддерживает эту задачу; список задач задан в manifest')
    stage = context.stage('recognition')
    result_path = reuse(existing_json, context, model, stage, root=ai_root)
    if result_path:
        data = read_result(result_path, context, model=model)
        options = data.get('recognition_options', {})
        expected = {'beam_size': beam_size, 'vad_filter': vad, 'word_timestamps': True,
                    'initial_prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest() if prompt else None}
        if (data.get('task') != task or data.get('requested_language') != (language if language != 'auto' else None)
                or any(options.get(k) != value for k, value in expected.items())):
            raise ValueError('Готовый результат не соответствует настройкам распознавания')
        # Render requested exports through the canonical formatter, without another inference.
        import importlib.util
        spec = importlib.util.spec_from_file_location('_speech_whisper_formats', WHISPER_RUNNER)
        formatter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(formatter)
        formats = ['txt'] if save_text else []
        formats += ['srt'] if srt else []
        formats += ['vtt'] if vtt else []
        paths = formatter.build_output_paths(context.source, stage, formats)
        formatter._write_outputs(paths, data, data['segments'])
        outputs = {'json': str(result_path), **{k: str(v) for k, v in paths.items()}}
    else:
        formats = ['json'] + (['txt'] if save_text else []) + (['srt'] if srt else []) + (['vtt'] if vtt else [])
        arguments = ['--input', str(context.source), '--output-dir', str(stage), '--model', str(model.path),
                     '--formats', ','.join(formats), '--device', device,
                     '--compute-type', 'int8_float16' if device == 'cuda' else 'int8' if device == 'cpu' else 'auto',
                     '--task', task, '--beam-size', str(beam_size), '--word-timestamps',
                     '--vad-filter' if vad else '--no-vad-filter']
        if language != 'auto':
            arguments += ['--language', language]
        if prompt:
            arguments += ['--initial-prompt', prompt]
        outputs = invoke('Whisper', arguments, ai_root=ai_root, cancel_check=cancel_check)
        result_path = Path(outputs['json'])
        data = read_result(result_path, context, model=model)
    published = {}
    for kind, enabled in [('json', save_json), ('txt', save_text), ('srt', srt), ('vtt', vtt)]:
        if enabled:
            published[kind] = str(publish(context, Path(outputs[kind]), '.' + kind))
    text = '\n'.join(item['text'].strip() for item in data['segments']).strip()
    return str(result_path), text, published


def diarize(context: Files, model: Model, *, device='auto', speakers=0, min_speakers=0,
            max_speakers=0, rttm=False, existing_json='', ai_root=AI_ROOT, cancel_check=None):
    stage = context.stage('diarization')
    result_path = reuse(existing_json, context, model, stage, root=ai_root)
    parameters = {key: value for key, value in [('num_speakers', speakers), ('min_speakers', min_speakers), ('max_speakers', max_speakers)] if value}
    if result_path:
        data = read_result(result_path, context, model=model)
        if data.get('parameters', {}) != parameters:
            raise ValueError('Готовая разметка не соответствует заданному числу спикеров')
        rttm_path = stage / 'diarization.rttm'
        if rttm:
            rows = []
            for segment in data['segments']:
                start, end = float(segment['start']), float(segment['end'])
                if start < 0 or end <= start or not segment['speaker'].strip() or any(c.isspace() for c in segment['speaker']):
                    raise ValueError('Недопустимый интервал в готовой разметке')
                rows.append(f"SPEAKER recording 1 {start:.3f} {end-start:.3f} <NA> <NA> {segment['speaker']} <NA> <NA>\n")
            rttm_path.write_text(''.join(rows), encoding='utf-8')
    else:
        result_path = stage / 'diarization.json'
        rttm_path = stage / 'diarization.rttm'
        arguments = ['--input', str(context.source), '--output-json', str(result_path), '--model', str(model.path), '--device', device]
        if rttm:
            arguments += ['--output-rttm', str(rttm_path)]
        for key, value in parameters.items():
            arguments += ['--' + key.replace('_', '-'), str(value)]
        invoke('SpeakerDiarization', arguments, ai_root=ai_root, cancel_check=cancel_check)
        data = read_result(result_path, context, model=model)
    rttm_export = publish(context, rttm_path, '.rttm') if rttm else None
    return str(result_path), len(data['speakers']), rttm_export


def extract(context: Files, model: Model, *, start=0.0, end=0.0, device='auto', ai_root=AI_ROOT, cancel_check=None):
    stage = context.stage('voiceprint')
    result = stage / 'voiceprint.json'
    arguments = ['extract', '--input', str(context.source), '--output-json', str(result),
                 '--model', str(model.path), '--device', device, '--start', str(start),
                 '--cache-dir', str(voiceprint_cache(model, ai_root))]
    if end > 0:
        arguments += ['--end', str(end)]
    invoke('Voiceprint', arguments, ai_root=ai_root, cancel_check=cancel_check)
    read_result(result, context)
    output = publish(context, result, '.voiceprint.json')
    return str(output)


def compare(context: Files, left: str, right: str, *, ai_root=AI_ROOT, cancel_check=None):
    stage = context.stage('comparison')
    result = stage / 'comparison.json'
    invoke('Voiceprint', ['compare', '--left', str(read_path(left, ai_root)), '--right', str(read_path(right, ai_root)),
                         '--output-json', str(result)], ai_root=ai_root, cancel_check=cancel_check)
    data = read_result(result)
    return str(publish(context, result, '.comparison.json')), float(data['cosine_similarity'])


def identify(context: Files, model: Model, diarization_json: str, reference_root: str, *, device='auto',
             threshold=0.25, minimum_margin=0.05, ai_root=AI_ROOT, cancel_check=None):
    diarization = read_path(diarization_json, ai_root)
    read_result(diarization, context)
    references = read_path(reference_root, ai_root) if reference_root.strip() else ai_root / 'Data' / 'Speakers'
    stage = context.stage('identification')
    result = stage / 'identification.json'
    invoke('Voiceprint', ['identify', '--input', str(context.source), '--diarization-json', str(diarization),
                         '--reference-root', str(references), '--output-json', str(result),
                         '--model', str(model.path), '--device', device, '--threshold', str(threshold),
                         '--minimum-margin', str(minimum_margin),
                         '--cache-dir', str(voiceprint_cache(model, ai_root))], ai_root=ai_root, cancel_check=cancel_check)
    read_result(result, context)
    return str(publish(context, result, '.identification.json'))


def transcript(context: Files, recognition_json: str, diarization_json: str, *, speaker_map='', confirmed=False,
               save_json=True, save_text=True, max_gap=1.0, ai_root=AI_ROOT, cancel_check=None):
    recognition, diarization = (read_path(path, ai_root) for path in (recognition_json, diarization_json))
    for path in (recognition, diarization):
        read_result(path, context)
    stage = context.stage('transcript')
    result = stage / 'transcript.json'
    text_file = stage / 'transcript.txt'
    arguments = ['--transcript-json', str(recognition), '--diarization-json', str(diarization),
                 '--output-json', str(result), '--output-txt', str(text_file), '--max-gap', str(max_gap)]
    if speaker_map.strip():
        arguments += ['--speaker-map-json', str(read_path(speaker_map, ai_root))]
    if confirmed:
        arguments += ['--speaker-map-confirmed']
    invoke('SpeakerTranscript', arguments, ai_root=ai_root, cancel_check=cancel_check)
    data = read_result(result, context)
    published = {}
    if save_json:
        published['json'] = str(publish(context, result, '.json'))
    if save_text:
        published['txt'] = str(publish(context, text_file, '.txt'))
    return str(result), text_file.read_text(encoding='utf-8'), published
