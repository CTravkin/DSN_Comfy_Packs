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

File Setup передает исходный файл связанным нодам и создает папку запуска в Output текущего экземпляра ComfyUI. Родительскую папку можно изменить через поле **Каталог результатов** в расширенных настройках File Setup.

По умолчанию в имя папки и основных файлов входят дата и время в формате `ГГГГ-ММ-ДД ЧЧ-ММ`, затем **Имя результата**, если оно задано. Если **Дата и время в имени** отключены, а поле имени пустое, используется имя исходного файла без расширения. При совпадении имен к новой папке добавляется номер.

Промежуточные файлы хранятся в `Data/` по этапам. Экспортируемые файлы записываются в корень папки запуска. Готовая разметка и распознавание повторно используются после проверки исходника, модели и настроек. Имена, полученные при поиске по голосам, остаются кандидатами до подтверждения карты вручную.

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

В [workflows/](workflows/) восемь готовых процессов. Каждый файл `.app.json` открывается как приложение с параметрами в боковой панели. Переключатель **Граф / Приложение** открывает граф того же процесса для просмотра и редактирования. Полный текст подсказки появляется при наведении на значок рядом с полем, а нажатие закрепляет ее.

Перед запуском указать исходный файл и выбрать установленные модели, если они требуются. В стандартной **Расшифровке** включены TXT, JSON и SRT, VTT доступен отдельно. **Транскрипт по карте** использует готовую разметку и карту имен. **Эталоны голосов** и **Карта имен** доступны в расширенных входных данных соответствующих нод.

Карточка **Результат** показывает папку запуска, файлы и краткую сводку, если она предусмотрена нодой. В графе карточка находится в ноде и показывает ее файлы. В приложении итоговые файлы собраны в общей карточке. **Поиск голосов** и **Транскрипт с именами** выводят отдельными группами JSON кандидатов и исходную разметку, чтобы их можно было использовать повторно.

Под карточкой приложение выводит штатный предпросмотр ComfyUI, когда нода его предоставляет. Файлы из Output доступны по ссылкам для скачивания. Для файлов вне Output отображаются имена и путь к папке запуска. При локальном подключении к ComfyUI на Windows кнопка **Открыть папку** открывает каталог результата текущего сеанса.

### Проверка

Из корня репозитория с Python экземпляра ComfyUI и корнем ComfyUI в `PYTHONPATH`.

```powershell
& '<Python экземпляра ComfyUI>' -B -m unittest discover -s Speech\Tests
```
