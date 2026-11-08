from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy_api.latest import io, ui

from .client import Request, generate
from .config import load_config
from .files import PROMPT_FORMATS, ResultSettings, collect_text, export_text, read_text


RequestOptions = io.Custom('DSN_TEXT_REQUEST')
SaveOptions = io.Custom('DSN_TEXT_SAVE')
NO_SERVER = 'Серверы не настроены'
NO_MODEL = 'Нет моделей'


def toggle(key, title, default=False, **kwargs):
    return io.Boolean.Input(key, display_name=title, default=default, label_on='Включено',
                            label_off='Выключено', **kwargs)


def digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


class RequestSetup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        servers = load_config().servers
        options = [
            io.DynamicCombo.Option(server.name, [
                io.Combo.Input('model', list(server.models), display_name='Модель',
                               tooltip='Модели выбранного сервера из конфига'),
                toggle('stop_after', 'Остановить после запроса',
                       tooltip='Остановка сервера, запущенного пакетом Text'),
                io.Float.Input('temperature', display_name='Температура', default=server.temperature,
                               min=0, max=2, step=0.01, advanced=True,
                               tooltip='Чем выше значение, тем разнообразнее ответ'),
                io.Int.Input('max_tokens', display_name='Лимит токенов ответа', default=server.max_tokens,
                             min=1, max=262144, advanced=True, tooltip='Максимальная длина ответа в токенах'),
            ]) for server in servers.values()
        ]
        if not options:
            options = [io.DynamicCombo.Option(NO_SERVER, [io.Combo.Input('model', [NO_MODEL], display_name='Модель')])]
        return io.Schema(node_id='DSNTextRequestSetup', display_name='Request Setup', category='Text',
                         inputs=[io.DynamicCombo.Input('profile', options, display_name='Сервер',
                                                       tooltip='Профиль подключения из конфига')],
                         outputs=[RequestOptions.Output('request', display_name='Настройки запроса')])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float('nan')

    @classmethod
    def execute(cls, profile):
        server = load_config().servers.get(profile['profile'])
        if server is None:
            raise ValueError('Добавьте профиль сервера и модели в конфиг Text')
        model = server.models.get(profile['model'])
        if model is None:
            raise ValueError('Выбранная модель отсутствует в профиле сервера')
        return io.NodeOutput(Request(server, model, profile.get('temperature', server.temperature),
                                     profile.get('max_tokens', server.max_tokens), profile.get('stop_after', False)))


class Prompt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='DSNTextPrompt', display_name='Prompt', category='Text', inputs=[
            io.DynamicCombo.Input('source', [
                io.DynamicCombo.Option('Текст', [io.String.Input(
                    'text', display_name='Промпт', default='', multiline=True, tooltip='Задание для модели')]),
                io.DynamicCombo.Option('Файл', [io.String.Input(
                    'file', display_name='Файл промпта', default='', tooltip='Путь к TXT или MD в каталоге промптов')]),
            ], display_name='Источник', tooltip='Текст из поля или файла'),
        ], outputs=[io.String.Output('prompt', display_name='Промпт')])

    @staticmethod
    def _read(source):
        if source['source'] == 'Файл':
            return read_text(load_config().prompts, source['file'], PROMPT_FORMATS)
        return source['text']

    @classmethod
    def fingerprint_inputs(cls, source):
        return digest(cls._read(source))

    @classmethod
    def execute(cls, source):
        prompt = cls._read(source)
        if not prompt.strip():
            raise ValueError('Промпт пуст')
        return io.NodeOutput(prompt)


class CollectText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='DSNTextCollect', display_name='Collect Text', category='Text', inputs=[
            io.String.Input('source', display_name='Исходный текст', optional=True, force_input=True,
                            tooltip='Дополнительный текст из графа'),
            io.String.Input('files', display_name='Файлы', default='', multiline=True,
                            tooltip='Пути к TXT, MD, JSON или CSV в Input Comfy, по одному на строку'),
        ], outputs=[io.String.Output('materials', display_name='Материалы')])

    @classmethod
    def fingerprint_inputs(cls, files='', **kwargs):
        return digest(collect_text(Path(folder_paths.get_input_directory()), files, ''))

    @classmethod
    def execute(cls, files='', source=''):
        return io.NodeOutput(collect_text(Path(folder_paths.get_input_directory()), files, source))


class ResultSetup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='DSNTextResultSetup', display_name='Result Setup', category='Text', inputs=[
            toggle('txt', 'Сохранить TXT', tooltip='Текстовый файл в каталоге результатов'),
            toggle('md', 'Сохранить MD', tooltip='Файл Markdown в каталоге результатов'),
            io.String.Input('name', display_name='Имя результата', default='',
                            tooltip='Общее имя папки запуска и файлов'),
            toggle('include_datetime', 'Дата и время в имени', True,
                   tooltip='Дата и время запуска в имени папки и файлов'),
            io.String.Input('directory', display_name='Каталог результатов', default='', advanced=True,
                            tooltip='Родительский каталог папки запуска, пустое поле означает Output Comfy'),
        ], outputs=[SaveOptions.Output('save', display_name='Настройки сохранения')])

    @classmethod
    def execute(cls, txt=False, md=False, name='', include_datetime=True, directory=''):
        output = Path(folder_paths.get_output_directory()).resolve()
        path = Path(directory.strip().strip('"')).expanduser()
        root = (output / path).resolve()
        if not path.is_absolute() and not root.is_relative_to(output):
            raise ValueError('Относительный каталог должен находиться в Output Comfy')
        return io.NodeOutput(ResultSettings(root, name, include_datetime, txt, md))


def saved_file(path, root, kind):
    relative = path.relative_to(root)
    return ui.SavedResult(relative.name, relative.parent.as_posix() if relative.parent.parts else '', kind)


def presentation(text, finish, exports, view_key):
    output = Path(folder_paths.get_output_directory()).resolve()
    files = [saved_file(path, output, io.FolderType.output) for path in exports if path.is_relative_to(output)]
    if not any(file['filename'].lower().endswith('.txt') for file in files):
        temp = Path(folder_paths.get_temp_directory()).resolve()
        directory = temp / 'text-preview'
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve() != directory:
            raise ValueError('Папка предпросмотра должна быть обычным каталогом')
        path = directory / f'{uuid4().hex}.txt'
        with path.open('x', encoding='utf-8', newline='') as stream:
            stream.write(text)
        preview = saved_file(path, temp, io.FolderType.temp)
        preview['display_name'] = 'Предпросмотр'
        files.insert(0, preview)
    summary = 'Ответ обрезан' if finish == 'length' else 'Текст готов'
    return {'text': [summary], 'files': files, 'dsn_text_result': [
        view_key, str(exports[0].parent) if exports else '', ', '.join(p.name for p in exports),
    ]}


class GenerateText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='DSNTextGenerate', display_name='Generate Text', category='Text', is_output_node=True,
                         inputs=[
                             RequestOptions.Input('request', display_name='Настройки запроса'),
                             io.String.Input('prompt', display_name='Промпт', force_input=True),
                             io.String.Input('materials', display_name='Материалы', optional=True, force_input=True),
                             SaveOptions.Input('save', display_name='Настройки сохранения', optional=True),
                         ], outputs=[io.String.Output('text', display_name='Текст'),
                                     io.String.Output('finish_reason', display_name='Причина завершения')],
                         hidden=[io.Hidden.extra_pnginfo])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float('nan')

    @classmethod
    async def execute(cls, request, prompt, materials='', save=None):
        if not prompt.strip():
            raise ValueError('Промпт пуст')
        started = datetime.now().astimezone()
        text, finish = await generate(request, prompt, materials, check_cancel=throw_exception_if_processing_interrupted)
        exports = export_text(text, save, started) if save else ()
        workflow = (cls.hidden.extra_pnginfo or {}).get('workflow') or {}
        view_key = (workflow.get('extra') or {}).get('dsn_text_view', '')
        return io.NodeOutput(text, finish, ui=presentation(text, finish, exports, view_key))


NODES = [RequestSetup, Prompt, CollectText, ResultSetup, GenerateText]
