# Speech

Пакет ComfyUI для локальной обработки аудио и видео. Семь нод можно использовать отдельно или соединять в одном графе. [DSN Comfy Packs](../README.md)

## Функции

| Нода | Назначение |
| --- | --- |
| File Setup | Общий исходный файл, имя и каталог результатов |
| Whisper | Распознавание с таймкодами слов, перевод на английский для совместимых моделей, экспорт JSON, TXT, SRT и VTT |
| Speaker Diarization | Разделение речи на анонимных спикеров, экспорт RTTM |
| Voiceprint | Отпечаток голоса по выбранному фрагменту |
| Voiceprint Compare | Сравнение двух отпечатков совместимой модели |
| Voiceprint Identity | Поиск кандидатов по подтвержденным эталонам голосов |
| Speaker Transcript | Транскрипт с репликами, временем и необязательной картой имен |

## Стек

Python, API нод ComfyUI V3, Faster Whisper и CTranslate2, pyannote.audio, SpeechBrain и TorchCodec. Модели работают в отдельных окружениях Python.

## Платформы

Windows x64. Для обработки доступны CPU и совместимая видеокарта NVIDIA.

## Требования

- ComfyUI с API нод V3. Порядок полей на полотне рассчитан на Nodes 2.0, Библиотека моделей использует систему ассетов ComfyUI.
- Python 3.14 для окружений обработки. Для диаризации и голосовых отпечатков требуется FFmpeg со сборкой shared и каталогом DLL в `PATH`.
- Для GPU в Whisper требуются cuBLAS для CUDA 12 и cuDNN 9. Для GPU в pyannote и SpeechBrain используется совместимая сборка PyTorch. [Faster Whisper](https://github.com/SYSTRAN/faster-whisper), [TorchCodec](https://github.com/meta-pytorch/torchcodec)

`SPEECH_AI_ROOT` задает каталог данных пакета. По умолчанию используется `~/.dsn-comfy/speech`. Переменная, если задана, должна быть доступна процессу ComfyUI.

| Путь относительно каталога данных | Назначение |
| --- | --- |
| `Apps/FasterWhisper/.venv`, `Apps/SpeakerAnalysis/.venv` | Окружения обработки |
| `Models/` | Локальные комплекты моделей с `manifest.json` |
| `Data/Speakers/` | Подтвержденные эталоны голосов |
| `Data/Comfy/`, `Cache/`, `Temp/` | Каталог моделей Comfy, кеши и временные файлы |

### Поддерживаемые модели

| Имя в ComfyUI | Семейство | Возможности | Hugging Face |
| --- | --- | --- | --- |
| Whisper Large V3 CT2 | `whisper` | Распознавание и перевод на английский | [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) |
| Whisper Large V3 Turbo CT2 | `whisper` | Распознавание | [dropbox-dash/faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) |
| Pyannote Speaker Diarization Community 1 | `diarization` | Разметка спикеров | [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) |
| SPKRec ECAPA VoxCeleb | `voiceprint` | Отпечатки и поиск по эталонам | [speechbrain/spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) |

Community 1 требует принятия условий доступа на Hugging Face. Для работы используются полные локальные снимки моделей, включая конфигурацию и сопутствующие файлы. Для загрузки через терминал нужен [Hugging Face CLI](https://huggingface.co/docs/huggingface_hub/guides/cli).

## Обмен данными

File Setup принимает путь к записи и передает его связанным нодам. Папка запуска по умолчанию создается в Output текущего экземпляра ComfyUI. Промежуточные результаты сохраняются в `Data/` внутри нее. Выбранные форматы экспорта попадают в корень папки запуска. Готовая разметка и распознавание повторно используются после проверки исходника, модели и настроек. Имена, полученные при поиске по голосам, остаются кандидатами до подтверждения карты вручную.

## Эксплуатация

### Установка

Подготовить два окружения. Пример для PowerShell с CPU сборкой PyTorch.

```powershell
$speechRoot = if ($env:SPEECH_AI_ROOT) {
    $env:SPEECH_AI_ROOT
} else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dsn-comfy\speech'
}
$whisper = Join-Path $speechRoot 'Apps\FasterWhisper\.venv'
$analysis = Join-Path $speechRoot 'Apps\SpeakerAnalysis\.venv'
py -3.14 -m venv $whisper
py -3.14 -m venv $analysis
& "$whisper\Scripts\python.exe" -m pip install faster-whisper==1.2.1 ctranslate2==4.8.1
& "$analysis\Scripts\python.exe" -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
& "$analysis\Scripts\python.exe" -m pip install torchcodec==0.11.1 pyannote.audio==4.0.7 speechbrain==1.1.0
```

Сохранить выбранные модели в отдельные каталоги внутри `Models/`. Подойдет полный снимок Hugging Face, полученный через `hf download <owner/repository> --revision <revision> --local-dir <каталог>`. В каждый каталог модели добавить `manifest.json`. Пример для Whisper Large V3.

```json
{
  "source": "Systran/faster-whisper-large-v3",
  "revision": "<revision>",
  "display_name": "Whisper Large V3 CT2",
  "family": "whisper",
  "weight_files": [{"path": "model.bin"}],
  "tasks": ["transcribe", "translate"]
}
```

Для остальных комплектов используются имена и семейства из таблицы. У Turbo указывается только `transcribe`. Для Community 1 в список весов входят `embedding/pytorch_model.bin` и `segmentation/pytorch_model.bin`. Для ECAPA указывается `embedding_model.ckpt`. Пути в списке относительны каталогу модели. Пакет читает список при запуске и показывает в нодах совместимые модели.

### Использование

В [workflows/](workflows/) восемь готовых процессов. Среди них есть отдельные графы для распознавания, разметки и работы с голосовыми отпечатками, а также три варианта транскрипта. **Транскрипт по карте** использует готовые разметку и карту имен.

Начать с поля **Исходный файл** в File Setup и выбрать установленные модели в нодах. В стандартной **Расшифровке** включены TXT, JSON и SRT, VTT доступен отдельно. **Эталоны голосов** и **Карта имен** доступны в расширенных входных данных соответствующих нод.

В графе нода показывает сводку и файлы запуска. В режиме приложения полный результат доступен в штатной панели ComfyUI. Ссылки открывают скачивание файлов из Output. Для другого каталога показываются имена и путь. Кнопка **Открыть папку** доступна для запуска в текущем сеансе при локальном подключении к ComfyUI на Windows.

### Проверка

Из корня репозитория с Python экземпляра ComfyUI и корнем ComfyUI в `PYTHONPATH`.

```powershell
& '<Python экземпляра ComfyUI>' -B -m unittest discover -s Speech\Tests
```
