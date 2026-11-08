from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy_api.latest import io, ui

from .client import Request, generate
from .config import load_config
from .files import PROMPT_FORMATS, ResultSettings, collect_text, export_text, list_material_files, read_text


RequestOptions = io.Custom('DSN_TEXT_REQUEST')
SaveOptions = io.Custom('DSN_TEXT_SAVE')
NO_SERVER = 'Серверы не настроены'
NO_MODEL = 'Нет моделей'
NO_FILE = 'Не выбран'
FILE_COUNTS = ['Без файлов', '1 файл', '2 файла', '3 файла', '4 файла',
               '5 файлов', '6 файлов', '7 файлов', '8 файлов']


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
                               tooltip='Модели, указанные для выбранного сервера в конфигурации пакета Text'),
                toggle('stop_after', 'Остановить после запроса',
                       tooltip='После запроса останавливается только сервер, запущенный пакетом Text'),
                io.Float.Input('temperature', display_name='Температура', default=server.temperature,
                               min=0, max=2, step=0.01, advanced=True,
                               tooltip='Низкие значения делают ответ более предсказуемым, высокие — более разнообразным'),
                io.Int.Input('max_tokens', display_name='Лимит токенов ответа', default=server.max_tokens,
                             min=1, max=262144, advanced=True,
                             tooltip='Максимальное число токенов, которое модель может сгенерировать для ответа'),
            ]) for server in servers.values()
        ]
        if not options:
            options = [io.DynamicCombo.Option(NO_SERVER, [io.Combo.Input('model', [NO_MODEL], display_name='Модель')])]
        return io.Schema(node_id='DSNTextRequestSetup', display_name='Request Setup', category='Text',
                         inputs=[io.DynamicCombo.Input('profile', options, display_name='Сервер',
                                                       tooltip='Профиль подключения к серверу из конфигурации пакета Text')],
                         outputs=[RequestOptions.Output('request', display_name='Настройки запроса')])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float('nan')

    @classmethod
    def execute(cls, profile):
        server = load_config().servers.get(profile['profile'])
        if server is None:
            raise ValueError('В конфигурации Text требуется профиль сервера с моделями')
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
                    'text', display_name='Промпт', default='', multiline=True,
                    tooltip='Задание и требования к ответу модели')]),
                io.DynamicCombo.Option('Файл', [io.String.Input(
                    'file', display_name='Файл промпта', default='',
                    tooltip='Относительный путь к файлу TXT или MD в каталоге промптов пакета Text')]),
            ], display_name='Источник', tooltip='Способ задания промпта: текст в поле или содержимое файла'),
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
        return io.Schema(node_id='DSNTextCollect', display_name='Collect Text', category='Text', is_deprecated=True, inputs=[
            io.String.Input('source', display_name='Исходный текст', optional=True, force_input=True,
                            tooltip='Дополнительный текст из графа'),
            io.String.Input('files', display_name='Файлы', default='', multiline=True,
                            tooltip='Относительные пути к файлам в общей папке Input, по одному на строку'),
        ], outputs=[io.String.Output('materials', display_name='Материалы')])

    @classmethod
    def fingerprint_inputs(cls, files='', **kwargs):
        return digest(collect_text(Path(folder_paths.get_input_directory()), files, ''))

    @classmethod
    def execute(cls, files='', source=''):
        return io.NodeOutput(collect_text(Path(folder_paths.get_input_directory()), files, source))


class TextContext(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        options = [NO_FILE, *list_material_files(Path(folder_paths.get_input_directory()))]
        counts = [
            io.DynamicCombo.Option(label, [
                io.Combo.Input(f'file_{index}', options, display_name=f'Файл {index}', default=NO_FILE,
                               tooltip='Файл TXT, MD, JSON или CSV из общей папки Input Comfy')
                for index in range(1, count + 1)
            ]) for count, label in enumerate(FILE_COUNTS)
        ]
        return io.Schema(
            node_id='DSNTextContext', display_name='Text Context', category='Text',
            description='Объединяет исходный текст и содержимое выбранных файлов в материалы для Generate Text.',
            inputs=[
                io.String.Input('source', display_name='Исходный текст', optional=True, force_input=True,
                                tooltip='Текст из графа перед выбранными файлами'),
                io.DynamicCombo.Input('files', counts, display_name='Количество файлов',
                                      tooltip='Открывает поля выбора файлов из общей папки Input. Материалы передаются по порядку полей'),
            ],
            outputs=[io.String.Output('materials', display_name='Материалы')],
        )

    @staticmethod
    def _read(files, source):
        names = [files.get(f'file_{index}') for index in range(1, len(FILE_COUNTS))]
        return collect_text(Path(folder_paths.get_input_directory()), [name for name in names if name and name != NO_FILE], source)

    @classmethod
    def fingerprint_inputs(cls, files, **kwargs):
        return digest(cls._read(files, ''))

    @classmethod
    def execute(cls, files, source=''):
        return io.NodeOutput(cls._read(files, source))


class ResultSetup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='DSNTextResultSetup', display_name='Result Setup', category='Text', inputs=[
            io.String.Input('directory', display_name='Каталог результатов', default='', advanced=True,
                            tooltip='Каталог для папок запуска. Пустое поле использует общий Output Comfy'),
            io.String.Input('name', display_name='Имя результата', default='',
                            tooltip='Общее имя папки запуска и файлов. При совпадении имени файлы добавляются в существующую папку'),
            toggle('include_datetime', 'Дата и время в имени', True,
                   tooltip='Добавляет дату и время запуска к имени папки и файлов'),
            toggle('txt', 'Сохранить TXT', True, tooltip='Сохраняет ответ в текстовый файл .txt'),
            toggle('md', 'Сохранить MD', True, tooltip='Сохраняет ответ в файл Markdown .md'),
        ], outputs=[SaveOptions.Output('save', display_name='Настройки сохранения')])

    @classmethod
    def execute(cls, directory='', name='', include_datetime=True, txt=True, md=True):
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
        text[:1600] + ('…' if len(text) > 1600 else ''),
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


NODES = [RequestSetup, Prompt, TextContext, ResultSetup, GenerateText, CollectText]
