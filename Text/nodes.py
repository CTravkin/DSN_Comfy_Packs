from datetime import datetime
from hashlib import sha256
from pathlib import Path
import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy_api.latest import io

from .client import Request, generate
from .config import load_config
from .files import PROMPT_FORMATS, ResultSettings, collect_text, export_text, read_text, resolve_path
from .results import presentation


RequestOptions = io.Custom('DSN_TEXT_REQUEST')
SaveOptions = io.Custom('DSN_TEXT_SAVE')
NO_SERVER = 'Серверы не настроены'
NO_MODEL = 'Нет моделей'
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
                io.Combo.Input('model', list(server.models), default=server.default_model, display_name='Модель',
                               tooltip='Модели, указанные для выбранного сервера в конфигурации'),
                toggle('stop_after', 'Остановить после запроса', server.stop_after,
                       tooltip='После запроса останавливается только сервер, запущенный этим пакетом'),
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
                         description='Задает сервер, модель и параметры запроса для Generate Text.',
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
                                     profile.get('max_tokens', server.max_tokens), profile.get('stop_after', server.stop_after)))


class Prompt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        options = [
                io.DynamicCombo.Option('Текст', [io.String.Input(
                    'text', display_name='Промпт', default='', multiline=True,
                    tooltip='Задание и требования к ответу модели')]),
                io.DynamicCombo.Option('Файл', [io.String.Input(
                    'file', display_name='Файл промпта', default='',
                    tooltip='Полный путь к TXT или MD либо путь от корня рабочей папки')]),
        ]
        if load_config().defaults.get('prompt', {}).get('source') == 'file':
            options.reverse()
        return io.Schema(node_id='DSNTextPrompt', display_name='Prompt', category='Text',
                         description='Берет промпт из поля или файла TXT/MD и передает его в Generate Text.', inputs=[
            io.DynamicCombo.Input('source', options, display_name='Источник',
                                  tooltip='Способ задания промпта: текст в поле или содержимое файла'),
        ], outputs=[io.String.Output('prompt', display_name='Промпт')])

    @staticmethod
    def _read(source):
        if source['source'] == 'Файл':
            config = load_config()
            return read_text(config.root, source['file'], PROMPT_FORMATS, prompts=config.prompts)
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
        return io.Schema(node_id='DSNTextCollect', display_name='Collect Text', category='Text', is_deprecated=True,
                         description='Собирает материалы из текста и файлов по списку путей. Для новых графов используется Text Context.',
                         inputs=[
            io.String.Input('source', display_name='Исходный текст', optional=True, force_input=True,
                            tooltip='Дополнительный текст из графа'),
            io.String.Input('files', display_name='Файлы', default='', multiline=True,
                            tooltip='Полные пути или пути от корня рабочей папки, по одному на строку'),
        ], outputs=[io.String.Output('materials', display_name='Материалы')])

    @classmethod
    def fingerprint_inputs(cls, files='', **kwargs):
        return digest(collect_text(load_config().root, files, ''))

    @classmethod
    def execute(cls, files='', source=''):
        return io.NodeOutput(collect_text(load_config().root, files, source))


class TextContext(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        counts = [
            io.DynamicCombo.Option(label, [
                io.String.Input(f'file_{index}', display_name=f'Файл {index}', default='',
                                tooltip='Полный путь к TXT, MD, JSON или CSV либо путь от корня рабочей папки')
                for index in range(1, count + 1)
            ]) for count, label in enumerate(FILE_COUNTS)
        ]
        default_count = load_config().defaults.get('context', {}).get('file_count', 0)
        counts.insert(0, counts.pop(default_count))
        return io.Schema(
            node_id='DSNTextContext', display_name='Text Context', category='Text',
            description='Собирает исходный текст и содержимое до восьми файлов в материалы для Generate Text.',
            inputs=[
                io.String.Input('source', display_name='Исходный текст', optional=True, force_input=True,
                                tooltip='Текст из графа, который передается перед содержимым файлов'),
                io.DynamicCombo.Input('files', counts, display_name='Количество файлов',
                                      tooltip='Материалы передаются в порядке указанных путей, пустые поля пропускаются'),
            ],
            outputs=[io.String.Output('materials', display_name='Материалы')],
        )

    @staticmethod
    def _read(files, source):
        names = [files.get(f'file_{index}') for index in range(1, len(FILE_COUNTS))]
        return collect_text(load_config().root, [name for name in names if name and name != 'Не выбран'], source)

    @classmethod
    def fingerprint_inputs(cls, files, **kwargs):
        return digest(cls._read(files, ''))

    @classmethod
    def execute(cls, files, source=''):
        return io.NodeOutput(cls._read(files, source))


class ResultSetup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        defaults = load_config().defaults.get('result', {})
        export = defaults.get('export', {})
        return io.Schema(node_id='DSNTextResultSetup', display_name='Result Setup', category='Text',
                         description='Задает папку, имя и форматы сохранения ответа Generate Text.', inputs=[
            io.String.Input('directory', display_name='Каталог результатов', default='', advanced=True,
                            tooltip='Полный путь или путь от корня рабочей папки; пустое поле означает Output Comfy'),
            io.String.Input('name', display_name='Имя результата', default='',
                            tooltip='Общее имя папки и файлов; при совпадении файлы добавляются в существующую папку'),
            toggle('include_datetime', 'Дата и время в имени', defaults.get('include_datetime', True),
                   tooltip='Добавляет дату и время запуска к имени папки и файлов'),
            toggle('txt', 'Сохранить TXT', export.get('txt', True), tooltip='Текстовый файл в каталоге результатов'),
            toggle('md', 'Сохранить MD', export.get('md', True), tooltip='Файл Markdown в каталоге результатов'),
        ], outputs=[SaveOptions.Output('save', display_name='Настройки сохранения')])

    @classmethod
    def execute(cls, directory='', name='', include_datetime=True, txt=True, md=True):
        root = (resolve_path(load_config().root, directory) if directory.strip()
                else Path(folder_paths.get_output_directory()).resolve())
        return io.NodeOutput(ResultSettings(root, name, include_datetime, txt, md))


class GenerateText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='DSNTextGenerate', display_name='Generate Text', category='Text', is_output_node=True,
                         description=('Передает выбранной модели промпт и материалы, возвращает текст ответа и причину завершения. '
                                      'При включенном экспорте сохраняет ответ в файл.'),
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
