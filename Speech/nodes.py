from __future__ import annotations

import json
import threading

import folder_paths
from comfy_api.latest import io

from . import catalog, files, results, runtime

FileContext = io.Custom('SPEECH_FILES')
_MODEL_LOCK = threading.Lock()
DEVICES = {'Автоматически': 'auto', 'Видеокарта': 'cuda', 'Процессор': 'cpu'}
LANGUAGES = {'Автоматически': 'auto', 'Русский': 'ru', 'Английский': 'en', 'Немецкий': 'de', 'Французский': 'fr', 'Испанский': 'es', 'Другой': None}


DEFAULTS = catalog.CONFIG.defaults


def boolean(key, label, default=False, **kwargs):
    return io.Boolean.Input(key, display_name=label, default=default,
                            label_on='Включено', label_off='Выключено', **kwargs)


def context_input():
    return FileContext.Input('files', display_name='Работа с файлами', tooltip='Источник и настройки сохранения из File Setup')


def model_input(family):
    models = [model for model in catalog.load_models() if model.family == family]
    names = [model.display_name for model in models]
    preferred = DEFAULTS['models'][family]
    matches = [model.display_name for model in models if preferred in (model.id, model.display_name)]
    default = matches[0] if len(matches) == 1 else names[0] if names else None
    return io.Combo.Input('model', options=names, display_name='Модель', default=default,
                          tooltip='Совместимые установленные комплекты из библиотеки моделей')


def device_input():
    device = DEFAULTS['device'] or 'auto'
    label = next(label for label, value in DEVICES.items() if value == device)
    return io.Combo.Input('device', options=list(DEVICES), display_name='Устройство', default=label, advanced=True,
                          tooltip='Автоматический режим использует GPU, если Comfy поддерживает его в текущем окружении, иначе CPU')


def string_output(key, label):
    return io.String.Output(key, display_name=label)


def schema(node_id, title, inputs, outputs, description, aliases=(), output=True):
    return io.Schema(node_id=node_id, display_name=title, category=catalog.CATEGORY, inputs=inputs, outputs=outputs,
                     description=description, search_aliases=list(aliases), is_output_node=output)


def heavy(function, *args, **kwargs):
    import comfy.model_management as memory
    while not _MODEL_LOCK.acquire(timeout=0.25):
        memory.throw_exception_if_processing_interrupted()
    try:
        memory.throw_exception_if_processing_interrupted()
        memory.unload_all_models()
        memory.soft_empty_cache()
        if kwargs.get('device') == 'auto':
            kwargs['device'] = 'cuda' if memory.get_torch_device().type == 'cuda' else 'cpu'
        return function(*args, **kwargs, cancel_check=memory.throw_exception_if_processing_interrupted)
    finally:
        _MODEL_LOCK.release()


class FileSetup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return schema('FileSetup', 'File Setup', [
            io.String.Input('media_path', display_name='Исходный файл', default='', tooltip='Полный путь к записи или JSON отпечатка либо путь от корня рабочей папки'),
            io.String.Input('output_directory', display_name='Каталог результатов', default='', advanced=True,
                            tooltip='Полный путь или путь от корня рабочей папки; пустое поле означает Output Comfy'),
            io.String.Input('name', display_name='Имя результата', default='', tooltip='Общее имя папки и основных файлов, при совпадении имени файла добавляется номер'),
            boolean('include_datetime', 'Дата и время в имени', DEFAULTS['file_setup']['include_datetime'],
                    tooltip='Дата и время добавляются к имени папки запуска и основных файлов'),
        ], [FileContext.Output('files', display_name='Работа с файлами'),
            string_output('directory', 'Каталог результатов'), string_output('source', 'Исходный файл')],
            'Настройка источника и общей папки, при совпадении имени папка используется повторно, файлы с занятыми именами сохраняются с номером.',
            ['файл', 'сохранение', 'источник'], output=False)

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float('nan')

    @classmethod
    def execute(cls, media_path, output_directory='', name='', include_datetime=True):
        context = files.setup_files(media_path, output_directory.strip() or folder_paths.get_output_directory(), name, include_datetime)
        return io.NodeOutput(context, str(context.directory), str(context.source))


class Whisper(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        defaults = DEFAULTS['whisper']
        language = defaults['language'] or 'auto'
        language_label = next((label for label, code in LANGUAGES.items() if code == language), 'Другой')
        task_label = 'Перевести на английский' if defaults['task'] == 'translate' else 'Распознать речь'
        export = defaults['export']
        return schema('WhisperTranscribe', 'Whisper', [context_input(), model_input('whisper'),
            io.Combo.Input('language', options=list(LANGUAGES), display_name='Язык', default=language_label,
                           tooltip='Язык исходной записи, в автоматическом режиме его определяет модель'),
            io.Combo.Input('task', options=['Распознать речь', 'Перевести на английский'], display_name='Задача', default=task_label,
                           tooltip='Распознавание сохраняет исходный язык; перевод на английский доступен для совместимых моделей'),
            io.String.Input('prompt', display_name='Имена и термины', default='', multiline=True, optional=True,
                            tooltip='Правильное написание имен, названий и терминов для распознавания'),
            boolean('save_json', 'Сохранить JSON', export['json'], tooltip='Копия JSON распознавания в каталоге результатов'),
            boolean('save_text', 'Сохранить TXT', export['txt'], tooltip='Текстовый файл в каталоге результатов'),
            boolean('srt', 'Субтитры SRT', export['srt'], tooltip='Субтитры в формате SubRip'),
            boolean('vtt', 'Субтитры VTT', export['vtt'], tooltip='Субтитры в формате WebVTT'), device_input(),
            io.String.Input('language_code', display_name='Код языка', default=language if language_label == 'Другой' else '', optional=True, advanced=True, tooltip='Код ISO 639-1 для варианта «Другой»'),
            io.String.Input('existing_json', display_name='Готовый результат', default='', optional=True, advanced=True, tooltip='JSON распознавания для той же записи, модели и настроек; повторная загрузка модели не требуется'),
            io.Int.Input('beam_size', display_name='Ширина поиска', default=defaults['beam_size'], min=1, max=50, optional=True, advanced=True,
                         tooltip='Количество вариантов при декодировании; большее значение может замедлить распознавание'),
            boolean('vad', 'Фильтрация участков без речи', defaults['vad'], optional=True, advanced=True,
                    tooltip='Участки без речи исключаются из распознавания'),
        ], [string_output('recognition_json', 'Распознанная речь'), string_output('text', 'Текст'),
            string_output('exports', 'Сохраненные файлы')],
            'Локальное распознавание речи с таймкодами слов; промежуточный JSON сохраняется в Data, дополнительные файлы создаются по выбранным форматам.',
            ['распознавание', 'субтитры', 'transcribe', 'turbo'])

    @classmethod
    def execute(cls, files, model, language='Автоматически', task='Распознать речь', prompt='', save_json=False,
                save_text=False, srt=False, vtt=False, device='Автоматически', language_code='', existing_json='', beam_size=5, vad=True):
        code = LANGUAGES[language] if language != 'Другой' else language_code.strip().lower()
        if not code:
            raise ValueError('Для языка «Другой» требуется код языка')
        path, text, exports = heavy(runtime.whisper, files, catalog.select_model(model, 'whisper'), language=code,
            task='translate' if task == 'Перевести на английский' else 'transcribe', prompt=prompt, device=DEVICES[device],
            save_json=save_json, save_text=save_text, srt=srt, vtt=vtt, existing_json=existing_json, beam_size=beam_size, vad=vad)
        detail = '' if text.strip() else 'Речь не найдена'
        return io.NodeOutput(path, text, json.dumps(exports, ensure_ascii=False),
                             ui=results.presentation(cls, files, detail or 'Расшифровка готова',
                                                     exports.values(), text=text, detail=detail))


class SpeakerDiarization(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        defaults = DEFAULTS['diarization']
        return schema('SpeakerDiarization', 'Speaker Diarization', [context_input(), model_input('diarization'),
            io.Int.Input('speakers', display_name='Число спикеров', default=defaults['speakers'] or 0, min=0, max=100,
                         tooltip='При значении 0 число спикеров определяется автоматически'),
            boolean('rttm', 'Сохранить RTTM', defaults['export']['rttm'], tooltip='Файл RTTM с интервалами реплик спикеров'), device_input(),
            io.Int.Input('min_speakers', display_name='Минимум спикеров', default=defaults['min_speakers'], min=0, max=100, optional=True, advanced=True, tooltip='0 — без ограничения'),
            io.Int.Input('max_speakers', display_name='Максимум спикеров', default=defaults['max_speakers'], min=0, max=100, optional=True, advanced=True, tooltip='0 — без ограничения'),
            io.String.Input('existing_json', display_name='Готовая разметка', default='', optional=True, advanced=True,
                            tooltip='JSON разметки той же записи, модели и настроек для повторного использования'),
        ], [string_output('diarization_json', 'Разметка спикеров'), io.Int.Output('speaker_count', display_name='Найдено спикеров')],
            'Разметка интервалов речи по анонимным спикерам без определения имен участников.', ['диаризация', 'спикеры', 'pyannote'])

    @classmethod
    def execute(cls, files, model, speakers=0, rttm=False, device='Автоматически', min_speakers=0, max_speakers=0, existing_json=''):
        path, count, rttm_export = heavy(runtime.diarize, files, catalog.select_model(model, 'diarization'),
            device=DEVICES[device], speakers=speakers, min_speakers=min_speakers, max_speakers=max_speakers,
            rttm=rttm, existing_json=existing_json)
        summary = f'Найдено спикеров: {count}'
        try:
            segments = runtime.read_result(runtime.read_path(path))['segments']
            lines = [f'{float(segment["start"]):.2f}–{float(segment["end"]):.2f} с · {segment["speaker"]}' for segment in segments]
        except (OSError, KeyError, TypeError, ValueError):
            lines = []
        artifacts = [path]
        if rttm_export:
            artifacts.append(rttm_export)
        return io.NodeOutput(path, count, ui=results.presentation(cls, files, summary, artifacts,
            text='\n'.join(lines) or 'Речевые интервалы не найдены', detail=summary))


class Voiceprint(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        defaults = DEFAULTS['voiceprint']
        return schema('VoiceprintExtract', 'Voiceprint', [context_input(), model_input('voiceprint'),
            io.Float.Input('start', display_name='Начало фрагмента, с', default=defaults['start'], min=0, step=0.1,
                           tooltip='Время от начала записи, 0 означает начало файла'),
            io.Float.Input('end', display_name='Конец фрагмента, с', default=defaults['end'] or 0, min=0, step=0.1, tooltip='0 — до конца записи'), device_input(),
        ], [string_output('voiceprint_json', 'Голосовой отпечаток')],
            'Голосовой отпечаток фрагмента с одним спикером; для эталона используются запись с подтвержденным именем и каталог из File Setup.', ['голос', 'отпечаток', 'ecapa'])

    @classmethod
    def execute(cls, files, model, start=0, end=0, device='Автоматически'):
        path = heavy(runtime.extract, files, catalog.select_model(model, 'voiceprint'), start=start, end=end, device=DEVICES[device])
        interval = runtime.read_result(runtime.read_path(path))['time_range']
        seconds = f'{interval["start"]:.2f}–{interval["end"]:.2f}'.replace('.', ',')
        return io.NodeOutput(path, ui=results.presentation(cls, files, 'Голосовой отпечаток готов', [path],
                                                           detail=f'Фрагмент: {seconds} с'))


class VoiceprintCompare(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return schema('VoiceprintCompare', 'Voiceprint Compare', [context_input(),
            io.String.Input('left', display_name='Первый отпечаток', default='',
                            tooltip='Путь к JSON первого отпечатка или связь с выходом «Исходный файл» в File Setup'),
            io.String.Input('right', display_name='Второй отпечаток', default='',
                            tooltip='Путь к JSON второго отпечатка той же модели'),
        ], [string_output('comparison_json', 'Результат сравнения'), io.Float.Output('similarity', display_name='Сходство')],
            'Сравнение двух совместимых голосовых отпечатков; оценка сходства не подтверждает личность.', ['сравнение голосов', 'similarity'])

    @classmethod
    def execute(cls, files, left, right):
        from comfy.model_management import throw_exception_if_processing_interrupted
        path, score = runtime.compare(files, left, right, cancel_check=throw_exception_if_processing_interrupted)
        similarity = f'{score:.4f}'.replace('.', ',')
        summary = f'Сходство: {similarity}'
        return io.NodeOutput(path, score, ui=results.presentation(cls, files, summary, [path], detail=summary))


class VoiceprintIdentify(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        defaults = DEFAULTS['voiceprint_identity']
        return schema('VoiceprintIdentify', 'Voiceprint Identity', [context_input(), model_input('voiceprint'),
            io.String.Input('diarization_json', display_name='Разметка спикеров', default='',
                            tooltip='Связь с Speaker Diarization или путь к готовому JSON разметки'),
            io.String.Input('reference_root', display_name='Эталоны голосов', default='', advanced=True,
                            tooltip='Каталог подтвержденных отпечатков; пустое поле использует путь из настроек пакета'),
            device_input(),
            io.Float.Input('threshold', display_name='Порог кандидата', default=defaults['threshold'], min=-1, max=1, step=0.01, optional=True,
                           tooltip='Минимальная оценка сходства для предложения имени'),
            io.Float.Input('minimum_margin', display_name='Минимальный отрыв', default=defaults['minimum_margin'], min=0, max=2, step=0.01, optional=True,
                           tooltip='Минимальная разница между оценками двух лучших кандидатов'),
        ], [string_output('identification_json', 'Кандидаты по голосам')],
            'Поиск кандидатов по подтвержденным эталонам; при недостаточной уверенности спикер остается неизвестным.', ['эталоны', 'идентификация', 'ecapa'])

    @classmethod
    def execute(cls, files, model, diarization_json, reference_root, device='Автоматически', threshold=0.25, minimum_margin=0.05):
        path = heavy(runtime.identify, files, catalog.select_model(model, 'voiceprint'), diarization_json, reference_root,
                     device=DEVICES[device], threshold=threshold, minimum_margin=minimum_margin)
        data = runtime.read_result(runtime.read_path(path))
        candidates = data['mapping']
        summary = f'Спикеров с кандидатами: {len(candidates)} из {len(data["speakers"])}'
        lines = [f'{speaker} · {candidates.get(speaker) or "Кандидат не найден"}' for speaker in data['speakers']]
        return io.NodeOutput(path, ui=results.presentation(cls, files, summary, [path],
            text='\n'.join(lines) or 'Спикеры не найдены', detail=summary))


class SpeakerTranscript(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        defaults = DEFAULTS['speaker_transcript']
        export = defaults['export']
        return schema('SpeakerTranscript', 'Speaker Transcript', [context_input(),
            io.String.Input('recognition_json', display_name='Распознанная речь', default='',
                            tooltip='Связь с Whisper или путь к готовому JSON распознавания'),
            io.String.Input('diarization_json', display_name='Разметка спикеров', default='',
                            tooltip='Связь с Speaker Diarization или путь к готовому JSON разметки'),
            io.String.Input('speaker_map', display_name='Карта имен', default='', optional=True, advanced=True,
                            tooltip='Путь к JSON карты имен для выбранной разметки'),
            boolean('confirmed', 'Подтверждение имен', advanced=True, tooltip='Включается после ручной проверки имен в карте'),
            boolean('save_json', 'Сохранить JSON', export['json'], tooltip='Копия JSON транскрипта в каталоге результатов'), boolean('save_text', 'Сохранить TXT', export['txt'], tooltip='Текстовый файл в каталоге результатов; выход «Текст» доступен независимо от экспорта'),
            io.Float.Input('max_gap', display_name='Объединять паузы, с', default=defaults['max_gap'], min=0, max=60, step=0.1, optional=True,
                           tooltip='Максимальная пауза для объединения соседних реплик одного спикера'),
        ], [string_output('transcript_json', 'Транскрипт'), string_output('text', 'Текст'), string_output('exports', 'Сохраненные файлы')],
            'Объединение распознанного текста с репликами спикеров; без ручного подтверждения имена отмечаются как кандидаты.', ['транскрипт', 'расшифровка', 'спикеры'])

    @classmethod
    def execute(cls, files, recognition_json, diarization_json, speaker_map='', confirmed=False, save_json=True, save_text=True, max_gap=1):
        from comfy.model_management import throw_exception_if_processing_interrupted
        path, text, exports = runtime.transcript(files, recognition_json, diarization_json, speaker_map=speaker_map,
            confirmed=confirmed, save_json=save_json, save_text=save_text, max_gap=max_gap,
            cancel_check=throw_exception_if_processing_interrupted)
        detail = '' if text.strip() else 'Реплики не найдены'
        return io.NodeOutput(path, text, json.dumps(exports, ensure_ascii=False),
                             ui=results.presentation(cls, files, detail or 'Транскрипт готов',
                                                     exports.values(), text=text, detail=detail))


NODES = [FileSetup, Whisper, SpeakerDiarization, Voiceprint, VoiceprintCompare, VoiceprintIdentify, SpeakerTranscript]
