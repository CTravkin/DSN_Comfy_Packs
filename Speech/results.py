from __future__ import annotations

from collections import OrderedDict
from ipaddress import ip_address
import json
import logging
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
_MAX_FOLDERS = 256
_FILE_SUFFIXES = {'.txt', '.json', '.srt', '.vtt', '.rttm'}


def _folder_token(directory: Path) -> str | None:
    if os.name != 'nt':
        return None
    token = token_urlsafe(24)
    with _folder_lock:
        _folders[token] = directory
        if len(_folders) > _MAX_FOLDERS:
            _folders.popitem(last=False)
    return token


def _saved_file(path: Path, root: Path, kind: io.FolderType) -> ui.SavedResult | None:
    if not path.is_relative_to(root):
        return None
    relative = path.relative_to(root)
    subfolder = relative.parent.as_posix() if relative.parent.parts else ''
    return ui.SavedResult(relative.name, subfolder, kind)


def _preview(text: str) -> ui.SavedResult:
    root = Path(folder_paths.get_temp_directory()).resolve()
    directory = root / 'speech-preview'
    directory.mkdir(exist_ok=True)
    if directory.resolve().parent != root:
        raise OSError('Speech preview folder is outside Comfy temp')
    path = directory / f'{uuid4().hex}.txt'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)
    result = _saved_file(path, root, io.FolderType.temp)
    result['display_name'] = 'Предпросмотр'
    return result


def presentation(node, context, summary: str, artifacts=(), *, text: str | None = None):
    directory = context.directory.resolve()
    output = Path(folder_paths.get_output_directory()).resolve()
    exports, native_files = [], []
    has_saved_text = False
    for artifact in sorted(artifacts, key=lambda item: Path(item).suffix.lower() != '.txt'):
        path = Path(artifact).resolve()
        if not path.is_relative_to(directory) or path.suffix.lower() not in _FILE_SUFFIXES or not path.is_file():
            continue
        saved = _saved_file(path, output, io.FolderType.output)
        exports.append({'name': path.name, 'file': saved})
        if saved:
            native_files.append(saved)
            has_saved_text |= path.suffix.lower() == '.txt'
    if not has_saved_text or (text is not None and not text.strip()):
        try:
            native_files.insert(0, _preview(text or summary))
        except OSError as error:
            logging.warning('Speech preview unavailable: %s', type(error).__name__)
    metadata = getattr(getattr(node, 'hidden', None), 'extra_pnginfo', None)
    workflow = metadata.get('workflow') if isinstance(metadata, dict) else None
    extra = workflow.get('extra') if isinstance(workflow, dict) else None
    result = {
        'view_key': extra.get('speech_view_key') if isinstance(extra, dict) else None,
        'summary': summary,
        'directory': str(directory),
        'exports': exports,
        'folder_token': _folder_token(directory),
    }
    return {
        'text': [summary],
        'files': native_files,
        # Comfy counts dict entries in UI output arrays as individual artifacts.
        'speech_result': [json.dumps(result, ensure_ascii=False)],
    }


def is_local_request(request) -> bool:
    try:
        origin = urlsplit(request.headers.get('Origin', ''))
        return (ip_address(request.remote).is_loopback
                and origin.scheme in {'http', 'https'}
                and origin.hostname in {'localhost', '127.0.0.1', '::1'}
                and origin.netloc == request.host)
    except (TypeError, ValueError):
        return False


def install_routes(routes):
    from aiohttp import web

    @routes.post('/speech/results/open-folder')
    async def open_folder(request):
        if os.name != 'nt' or not is_local_request(request):
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
