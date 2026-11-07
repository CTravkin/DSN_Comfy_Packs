from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .catalog import AI_ROOT


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_path(value: str) -> Path:
    return Path(value.strip().strip('"')).expanduser().resolve()


def safe_name(value: str) -> str:
    value = value.strip()
    if (not value or value in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.endswith(('.', ' ')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I)):
        raise ValueError('Название содержит недопустимые символы или зарезервированное имя Windows')
    if len(value) > 160:
        raise ValueError('Имя результата должно содержать до 160 символов')
    return value


@dataclass(frozen=True)
class Files:
    source: Path
    source_sha256: str
    directory: Path
    name: str

    @property
    def data(self) -> Path:
        return self.directory / 'Data'

    def validate_source(self):
        if not self.source.is_file() or sha256(self.source) != self.source_sha256:
            raise ValueError('Исходный файл изменился; требуется новый запуск через File Setup')

    def stage(self, name: str) -> Path:
        self.validate_source()
        for index in range(1, 10000):
            folder = self.data / (name if index == 1 else f'{name}-{index}')
            try:
                folder.mkdir()
                return folder
            except FileExistsError:
                continue
        raise FileExistsError('Не удалось выделить каталог этапа')


def setup_files(media_path: str, output_directory: str = '',
                name: str = '', include_datetime: bool = True, *, now: datetime | None = None) -> Files:
    source = read_path(media_path)
    if not source.is_file():
        raise FileNotFoundError(f'Файл записи не найден: {source}')
    if not output_directory.strip():
        output_directory = str(AI_ROOT / 'Output')
    output = read_path(output_directory)
    user_name = safe_name(name) if name.strip() else ''
    stamp = (now or datetime.now().astimezone()).strftime('%Y-%m-%d %H-%M')
    stem = safe_name(' '.join(x for x in (stamp if include_datetime else '', user_name) if x) or source.stem)
    fingerprint = sha256(source)
    output.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10000):
        basename = stem if index == 1 else f'{stem} ({index})'
        directory = output / basename
        try:
            directory.mkdir()
            (directory / 'Data').mkdir()
            return Files(source, fingerprint, directory, basename)
        except FileExistsError:
            continue
    raise FileExistsError('Не удалось выделить папку результата')


def publish(context: Files, source: Path, extension: str) -> Path:
    context.validate_source()
    if not re.fullmatch(r'(?:\.[\w-]+)+', extension):
        raise ValueError('Недопустимое расширение результата')
    target = context.directory / (context.name + extension)
    created = False
    try:
        with source.open('rb') as original:
            with target.open('xb') as destination:
                created = True
                shutil.copyfileobj(original, destination)
    except FileExistsError:
        raise FileExistsError(f'Экспорт уже существует: {target.name}; для повторного экспорта требуется отдельный запуск') from None
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return target
