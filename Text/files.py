from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re


PROMPT_FORMATS = {'.txt', '.md'}
MATERIAL_FORMATS = PROMPT_FORMATS | {'.json', '.csv'}


def read_text(root: Path, name: str, formats: set[str]) -> str:
    root = root.resolve()
    relative = Path(name.strip().strip('"'))
    path = (root / relative).resolve()
    if relative.anchor or not path.is_relative_to(root) or path.suffix.lower() not in formats:
        raise ValueError('Укажите относительный путь к файлу поддерживаемого формата')
    if not path.is_file():
        raise ValueError(f'Файл не найден: {relative.name}')
    try:
        return path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        raise ValueError(f'Не удалось прочитать файл UTF-8: {relative.name}') from None


def collect_text(root: Path, file_list: str, source: str) -> str:
    parts = [source] if source.strip() else []
    for name in file_list.splitlines():
        if name.strip():
            text = read_text(root, name, MATERIAL_FORMATS)
            filename = Path(name.strip().strip('"')).name
            parts.append(f'## {filename}\n\n{text}')
    return '\n\n'.join(parts)


@dataclass(frozen=True)
class ResultSettings:
    root: Path
    name: str = ''
    include_datetime: bool = True
    txt: bool = False
    md: bool = False


def export_text(text: str, settings: ResultSettings, now: datetime | None = None) -> tuple[Path, ...]:
    extensions = [ext for ext, enabled in (('.txt', settings.txt), ('.md', settings.md)) if enabled]
    if not extensions:
        return ()
    name = settings.name.strip()
    if (name in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith('.')
            or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I) or len(name) > 140):
        raise ValueError('Имя результата недопустимо')
    stamp = (now or datetime.now().astimezone()).strftime('%Y-%m-%d %H-%M')
    stem = ' '.join(part for part in (stamp if settings.include_datetime else '', name) if part) or 'Результат'
    directory = settings.root.resolve() / stem
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError('Папка результата должна быть обычным каталогом')
    for index in range(1, 10000):
        base = stem if index == 1 else f'{stem} ({index})'
        created = []
        try:
            for ext in extensions:
                path = directory / (base + ext)
                with path.open('x', encoding='utf-8', newline='') as stream:
                    created.append(path)
                    stream.write(text)
        except FileExistsError:
            for path in created:
                path.unlink()
        except BaseException:
            for path in created:
                path.unlink()
            raise
        else:
            return tuple(created)
    raise FileExistsError('Не удалось подобрать свободное имя файла результата')
