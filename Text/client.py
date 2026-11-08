import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
from threading import Lock
from urllib.parse import urlsplit

import aiohttp

from .config import Server


@dataclass(frozen=True)
class Request:
    server: Server
    model: str
    temperature: float
    max_tokens: int
    stop_after: bool = False


_locks: dict[str, Lock] = {}
_owned: dict[str, subprocess.Popen] = {}


async def fetch_json(session, server, route, timeout, *, payload=None):
    method = 'POST' if payload is not None else 'GET'
    async with session.request(method, server.url + route, json=payload, allow_redirects=False,
                               timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        response.raise_for_status()
        if response.status != 200:
            raise RuntimeError(f'Сервер «{server.name}» вернул HTTP {response.status}')
        return await response.json(content_type=None)


async def interruptible(coroutine, check_cancel):
    task = asyncio.create_task(coroutine)
    try:
        while not task.done():
            check_cancel()
            await asyncio.wait({task}, timeout=0.2)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def start_process(server: Server) -> subprocess.Popen:
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    try:
        return subprocess.Popen(server.launch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **flags)
    except OSError:
        raise RuntimeError(f'Не удалось запустить сервер «{server.name}». Проверьте команду в конфиге') from None


async def stop_process(process: subprocess.Popen):
    def stop():
        if process.poll() is not None:
            return
        if os.name == 'nt':
            taskkill = Path(os.environ['SystemRoot']) / 'System32' / 'taskkill.exe'
            subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=8)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                raise
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    await asyncio.to_thread(stop)


async def models_when_ready(session, server, key, check_cancel):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + server.startup_timeout
    while True:
        check_cancel()
        process = _owned.get(key)
        if process is not None and process.poll() is not None:
            _owned.pop(key)
            raise RuntimeError(f'Сервер «{server.name}» завершился при запуске (код {process.returncode})')
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError(f'Сервер «{server.name}» не готов за {server.startup_timeout:g} с')
        try:
            return await interruptible(fetch_json(session, server, '/models', min(remaining, 5)), check_cancel)
        except aiohttp.ClientConnectorError as error:
            if process is None and server.launch and isinstance(error.os_error, ConnectionRefusedError):
                _owned[key] = start_process(server)
            elif process is None:
                raise
        except aiohttp.ClientResponseError as error:
            if error.status != 503:
                raise
        await asyncio.sleep(0.2)


async def generate(request: Request, prompt: str, materials: str = '', *, check_cancel=lambda: None):
    server = request.server
    key = urlsplit(server.url).netloc.lower()
    lock = _locks.setdefault(key, Lock())
    while not lock.acquire(blocking=False):
        check_cancel()
        await asyncio.sleep(0.1)
    try:
        if (process := _owned.get(key)) is not None and process.poll() is not None:
            _owned.pop(key)
        existing = _owned.get(key)
        ready = False
        try:
            headers = {}
            if server.api_key_env:
                secret = os.environ.get(server.api_key_env)
                if not secret:
                    raise RuntimeError(f'Не задана переменная ключа {server.api_key_env}')
                headers['Authorization'] = f'Bearer {secret}'
            async with aiohttp.ClientSession(headers=headers) as session:
                catalog = await models_when_ready(session, server, key, check_cancel)
                ready = True
                if request.model not in {item['id'] for item in catalog['data']}:
                    raise RuntimeError(f'Модель «{request.model}» недоступна на сервере «{server.name}»')
                messages = [{'role': 'user', 'content': prompt}]
                if materials.strip():
                    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': materials}]
                payload = {'model': request.model, 'messages': messages, 'stream': False,
                           'temperature': request.temperature, 'max_tokens': request.max_tokens}
                response = await interruptible(fetch_json(session, server, '/chat/completions', server.timeout,
                                                          payload=payload), check_cancel)
                choice = response['choices'][0]
                text = choice['message']['content']
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError(f'Сервер «{server.name}» вернул пустой текст')
                return text, choice['finish_reason'] or 'unknown'
        except aiohttp.ClientResponseError as error:
            raise RuntimeError(f'Сервер «{server.name}» вернул HTTP {error.status}') from None
        except aiohttp.ClientError:
            raise RuntimeError(f'Не удалось подключиться к серверу «{server.name}»') from None
        except TimeoutError:
            raise RuntimeError(f'Истекло время ожидания сервера «{server.name}»') from None
        except (ValueError, TypeError, KeyError, IndexError):
            raise RuntimeError(f'Сервер «{server.name}» вернул некорректный ответ') from None
        finally:
            process = _owned.get(key)
            if process is not None and (request.stop_after or (not ready and process is not existing)):
                try:
                    await stop_process(process)
                except (OSError, subprocess.SubprocessError):
                    raise RuntimeError(f'Не удалось остановить сервер «{server.name}»') from None
                else:
                    _owned.pop(key)
    finally:
        lock.release()
