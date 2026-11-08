from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_TEMPERATURE = 0.8
DEFAULT_MAX_TOKENS = 8000


@dataclass(frozen=True)
class Server:
    name: str
    url: str
    models: dict[str, str] = field(repr=False)
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float = 600
    startup_timeout: float = 30
    api_key_env: str = ''
    launch: tuple[str, ...] = field(default=(), repr=False)
    cwd: Path | None = None
    stop_after: bool = False
    default_model: str | None = None


@dataclass(frozen=True)
class Config:
    root: Path
    prompts: Path
    servers: dict[str, Server]
    models: dict[str, str]
    catalog: Path
    manifests: dict[str, Path] = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)


def config_locations():
    try:
        import folder_paths
    except ModuleNotFoundError as error:
        if error.name != 'folder_paths':
            raise
        return Path.cwd(), Path.home() / '.dsn-comfy' / 'text'
    return Path(folder_paths.base_path), Path(folder_paths.get_user_directory()) / 'package-configs'


def resolve_path(base: Path, value: str) -> Path:
    return (base / Path(value).expanduser()).resolve()


def section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f'Раздел {name} должен быть объектом')
    return value


def read_defaults(data: dict) -> dict:
    defaults = section(data, 'defaults')
    for name in ('connection', 'request', 'prompt', 'context', 'result'):
        section(defaults, name)
    if section(defaults, 'prompt').get('source', 'text') not in ('text', 'file'):
        raise ValueError('defaults.prompt.source должен быть text или file')
    count = section(defaults, 'context').get('file_count', 0)
    if type(count) is not int or not 0 <= count <= 8:
        raise ValueError('defaults.context.file_count должен быть целым числом от 0 до 8')
    result = section(defaults, 'result')
    if any(type(value) is not bool for value in
           (result.get('include_datetime', True), section(result, 'export').get('txt', True),
            section(result, 'export').get('md', True))):
        raise ValueError('Параметры сохранения должны быть true или false')
    if type(section(defaults, 'request').get('stop_after', False)) is not bool:
        raise ValueError('defaults.request.stop_after должен быть true или false')
    return defaults


def load_config(path: Path | None = None) -> Config:
    base, config_dir = config_locations()
    configured = os.environ.get('COMFY_TEXT_CONFIG') or os.environ.get('DSN_TEXT_CONFIG')
    explicit = path is not None or bool(configured)
    standard = config_dir / 'text-config.json'
    legacy = Path.home() / '.dsn-comfy' / 'text' / 'config.json'
    fallback = legacy if not standard.is_file() and legacy.is_file() else standard
    path = (path or (Path(configured) if configured else fallback)).expanduser().resolve()
    catalog = path.parent / 'text-model-catalog'
    if not explicit and not path.is_file():
        return Config(base, base / 'Prompts', {}, {}, catalog)
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        paths = section(data, 'paths')
        if data.get('root') is not None:
            root = resolve_path(path.parent, data['root'])
        elif 'root' not in data and ('prompts' in data or path == legacy):
            root = path.parent
        else:
            root = base
        prompts = resolve_path(root, paths.get('prompts') or data.get('prompts') or 'Prompts')
        if paths.get('model_catalog'):
            catalog = resolve_path(root, paths['model_catalog'])
        defaults = read_defaults(data)
        connection = section(defaults, 'connection')
        request = section(defaults, 'request')
        models = {}
        manifests = {}
        for model, record in section(data, 'models').items():
            models[model] = record['name'] if isinstance(record, dict) else record
            if isinstance(record, dict) and record.get('manifest'):
                manifests[model] = resolve_path(root, record['manifest'])
        if any(not isinstance(name, str) or not name.strip() for name in models.values()):
            raise ValueError('У моделей должны быть читаемые названия')
        if len(set(models.values())) != len(models):
            raise ValueError('Названия моделей должны быть уникальны')
        servers = {}
        profiles = section(data, 'servers')
        preferred = connection.get('server')
        if preferred is not None and preferred not in profiles:
            raise ValueError('defaults.connection.server отсутствует в servers')
        for key in sorted(profiles, key=lambda key: key != preferred):
            record = profiles[key]
            name = record.get('name', key)
            if not isinstance(name, str) or not name.strip() or name in servers:
                raise ValueError('Названия серверов должны быть непустыми и уникальными')
            url = record['url'].rstrip('/')
            endpoint = urlsplit(url)
            if (endpoint.scheme not in ('http', 'https') or not endpoint.hostname or endpoint.username
                    or endpoint.password or endpoint.query or endpoint.fragment):
                raise ValueError(f'Недопустимый адрес сервера {name}')
            endpoint.port
            launch = record.get('launch')
            cwd = None
            if isinstance(launch, dict):
                command = launch.get('executable')
                if not isinstance(command, str) or not command.strip():
                    raise ValueError(f'Укажите исполняемый файл для {name}')
                executable = Path(command).expanduser()
                if executable.is_absolute() or executable.parent != Path('.'):
                    executable = resolve_path(root, str(executable))
                cwd = resolve_path(root, launch['cwd']) if launch.get('cwd') else root
                arguments = launch.get('args', [])
                if not isinstance(arguments, list):
                    raise ValueError(f'Аргументы запуска {name} должны быть списком')
                launch = [str(executable), *arguments]
            if launch is None:
                launch = []
            if not isinstance(launch, list) or any(not isinstance(arg, str) or not arg for arg in launch):
                raise ValueError(f'Команда запуска {name} должна быть списком аргументов')
            if launch and endpoint.hostname not in ('127.0.0.1', '::1'):
                raise ValueError(f'Автозапуск {name} требует адреса 127.0.0.1 или ::1')
            available = {models[model]: value for model, value in record['models'].items()}
            if not available or any(not isinstance(value, str) or not value.strip() for value in available.values()):
                raise ValueError(f'Укажите API-имена моделей для {name}')
            temperature = float(record.get('temperature', request.get('temperature', DEFAULT_TEMPERATURE)))
            tokens = int(record.get('max_tokens', request.get('max_tokens', DEFAULT_MAX_TOKENS)))
            timeout = float(record.get('timeout', connection.get('timeout', 600)))
            startup = float(record.get('startup_timeout', connection.get('startup_timeout', 30)))
            if not (0 <= temperature <= 2 and 1 <= tokens <= 262144 and 0 < timeout <= 3600 and 0 < startup <= 300):
                raise ValueError(f'Недопустимые параметры генерации или таймауты для {name}')
            key_env = record.get('api_key_env') or ''
            if not isinstance(key_env, str):
                raise ValueError(f'Некорректное имя переменной ключа для {name}')
            default_model = record.get('default_model')
            if default_model is not None and default_model not in record['models']:
                raise ValueError(f'Модель по умолчанию отсутствует в профиле {name}')
            stop_after = record.get('stop_after', request.get('stop_after', False))
            if type(stop_after) is not bool:
                raise ValueError(f'stop_after для {name} должен быть true или false')
            servers[name] = Server(name, url, available, temperature, tokens, timeout, startup, key_env,
                                   tuple(launch), cwd, stop_after, models.get(default_model))
        return Config(root, prompts, servers, models, catalog, manifests, defaults)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f'Не удалось прочитать конфигурацию Text ({path.name}): {error}') from None
