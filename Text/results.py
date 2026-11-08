from collections import OrderedDict
from ipaddress import ip_address
import json
import os
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

import folder_paths
from comfy_api.latest import io, ui


_folders: OrderedDict[str, Path] = OrderedDict()
_folder_lock = Lock()


def _folder_token(directory: Path) -> str | None:
    if os.name != 'nt':
        return None
    token = token_urlsafe(24)
    with _folder_lock:
        _folders[token] = directory
        if len(_folders) > 256:
            _folders.popitem(last=False)
    return token


def _saved_file(path, root, kind):
    if path.is_relative_to(root):
        relative = path.relative_to(root)
        return ui.SavedResult(relative.name, relative.parent.as_posix() if relative.parent.parts else '', kind)


def presentation(text, finish, exports, view_key):
    output = Path(folder_paths.get_output_directory()).resolve()
    published = [{'name': path.name, 'file': _saved_file(path, output, io.FolderType.output)} for path in exports]
    files = [entry['file'] for entry in published if entry['file'] is not None]
    if not any(file['filename'].lower().endswith('.txt') for file in files):
        temp = Path(folder_paths.get_temp_directory()).resolve()
        directory = temp / 'text-preview'
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve() != directory:
            raise ValueError('Папка предпросмотра должна быть обычным каталогом')
        path = directory / f'{uuid4().hex}.txt'
        with path.open('x', encoding='utf-8', newline='') as stream:
            stream.write(text)
        preview = _saved_file(path, temp, io.FolderType.temp)
        preview['display_name'] = 'Предпросмотр'
        files.insert(0, preview)
    directory = exports[0].parent if exports else None
    result = {
        'view_key': view_key,
        'directory': str(directory) if directory else '',
        'exports': published,
        'folder_token': _folder_token(directory) if directory else None,
        'preview': text,
        'detail': 'Достигнут лимит токенов ответа' if finish == 'length' else '',
    }
    return {'files': files, 'dsn_text_result': [json.dumps(result, ensure_ascii=False)]}


def _local(request):
    try:
        origin = urlsplit(request.headers.get('Origin', ''))
        return (ip_address(request.remote).is_loopback
                and origin.scheme in {'http', 'https'}
                and origin.hostname in {'localhost', '127.0.0.1', '::1'}
                and origin.netloc == request.host)
    except (TypeError, ValueError):
        return False


def install():
    from aiohttp import web
    from server import PromptServer

    server = getattr(PromptServer, 'instance', None)
    if server is None:
        return

    @server.routes.post('/text/results/open-folder')
    async def open_folder(request):
        if os.name != 'nt' or not _local(request):
            return web.Response(status=403)
        try:
            payload = await request.json()
            token = payload.get('token') if isinstance(payload, dict) else None
        except ValueError:
            return web.Response(status=400)
        if not isinstance(token, str):
            return web.Response(status=400)
        with _folder_lock:
            directory = _folders.get(token)
        if directory is None or not directory.is_dir() or directory.resolve() != directory:
            return web.Response(status=404)
        try:
            os.startfile(str(directory))
        except OSError:
            return web.Response(status=503)
        return web.Response(status=204)
