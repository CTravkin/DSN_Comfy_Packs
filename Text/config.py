from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Server:
    name: str
    url: str
    models: dict[str, str] = field(repr=False)
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout: float = 600
    startup_timeout: float = 30
    api_key_env: str = ''
    launch: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Config:
    prompts: Path
    servers: dict[str, Server]


def load_config(path: Path | None = None) -> Config:
    configured = os.environ.get('DSN_TEXT_CONFIG')
    explicit = path is not None or bool(configured)
    path = (path or Path(configured or '~/.dsn-comfy/text/config.json')).expanduser().resolve()
    if not explicit and not path.is_file():
        return Config(path.parent / 'Prompts', {})
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        root = (path.parent / data.get('root', '.')).resolve()
        prompts = (root / data.get('prompts', 'Prompts')).resolve()
        models = data['models']
        if any(not isinstance(name, str) or not name.strip() for name in models.values()):
            raise ValueError('У моделей должны быть читаемые названия')
        if len(set(models.values())) != len(models):
            raise ValueError('Названия моделей должны быть уникальны')
        servers = {}
        for key, record in data['servers'].items():
            name = record.get('name', key)
            if not isinstance(name, str) or not name.strip() or name in servers:
                raise ValueError('Названия серверов должны быть непустыми и уникальными')
            url = record['url'].rstrip('/')
            endpoint = urlsplit(url)
            if (endpoint.scheme not in ('http', 'https') or not endpoint.hostname or endpoint.username
                    or endpoint.password or endpoint.query or endpoint.fragment):
                raise ValueError(f'Недопустимый адрес сервера {name}')
            endpoint.port
            launch = record.get('launch', [])
            if not isinstance(launch, list) or any(not isinstance(arg, str) or not arg for arg in launch):
                raise ValueError(f'Команда запуска {name} должна быть списком аргументов')
            if launch and endpoint.hostname not in ('127.0.0.1', '::1'):
                raise ValueError(f'Автозапуск {name} требует адреса 127.0.0.1 или ::1')
            available = {models[model]: value for model, value in record['models'].items()}
            if not available or any(not isinstance(value, str) or not value.strip() for value in available.values()):
                raise ValueError(f'Укажите API-имена моделей для {name}')
            temperature = float(record.get('temperature', 0.3))
            tokens = int(record.get('max_tokens', 1024))
            timeout = float(record.get('timeout', 600))
            startup = float(record.get('startup_timeout', 30))
            if not (0 <= temperature <= 2 and 1 <= tokens <= 262144 and 0 < timeout <= 3600 and 0 < startup <= 300):
                raise ValueError(f'Недопустимые параметры генерации или таймауты для {name}')
            key_env = record.get('api_key_env', '')
            if not isinstance(key_env, str):
                raise ValueError(f'Некорректное имя переменной ключа для {name}')
            servers[name] = Server(name, url, available, temperature, tokens, timeout, startup, key_env, tuple(launch))
        return Config(prompts, servers)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f'Не удалось прочитать конфиг Text ({path.name}): {error}') from None
