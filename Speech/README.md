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

- Порядок полей на полотне рассчитан на Nodes 2.0.
- Python 3.14 для окружений обработки. Для диаризации и голосовых отпечатков нужна сборка FFmpeg shared. Каталог с DLL указывается в `paths.ffmpeg_bin` или добавляется в `PATH`.
- Для GPU в Whisper требуются cuBLAS для CUDA 12 и cuDNN 9. Для GPU в pyannote и SpeechBrain используется совместимая сборка PyTorch. [Faster Whisper](https://github.com/SYSTRAN/faster-whisper), [TorchCodec](https://github.com/meta-pytorch/torchcodec)

### Конфигурация

Шаблон находится в [config.example.json](config.example.json). ComfyUI ищет рабочую копию в `package-configs/speech-config.json` внутри своего пользовательского каталога. Для другого размещения задать полный путь через `COMFY_SPEECH_CONFIG` в окружении процесса ComfyUI. Для прежних установок `SPEECH_AI_ROOT` применяется при отсутствии файла конфигурации и явно заданного пути к нему.

| Раздел | Назначение |
| --- | --- |
| `root` | Рабочая папка. При `null` используется базовая папка ComfyUI. Относительное значение `root` отсчитывается от папки конфига |
| `paths` | Каталоги моделей, эталонов, кешей и временных файлов, путь к FFmpeg и место хранения карточек и их аннотаций |
| `runtime` | Таймаут этапа в секундах, автономный режим Hugging Face и пути к Python и скриптам обработки |
| `defaults` | Начальные значения новых нод. Пустые модель, язык и устройство включают выбор совместимой модели, распознавание языка и автоматический выбор устройства |

Относительные пути в `paths` и `runtime` отсчитываются от рабочего корня. При `model_roots: null` пакет ищет манифесты в каталоге моделей ComfyUI. При `model_catalog: null` карточки хранятся в `speech-model-catalog` рядом с конфигом, а при `model_annotations: null` аннотации хранятся в `model-annotations.json` рядом с каталогом карточек.

### Поддерживаемые модели

| Имя в ComfyUI | Семейство | Возможности | Hugging Face |
| --- | --- | --- | --- |
| Whisper Large V3 CT2 | `whisper` | Распознавание и перевод на английский | [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) |
| Whisper Large V3 Turbo CT2 | `whisper` | Распознавание | [dropbox-dash/faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) |
| Pyannote Speaker Diarization Community 1 | `diarization` | Разметка спикеров | [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) |
| SPKRec ECAPA VoxCeleb | `voiceprint` | Отпечатки и поиск по эталонам | [speechbrain/spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) |

Community 1 требует принятия условий доступа на Hugging Face. Для работы используются полные локальные снимки моделей, включая конфигурацию и сопутствующие файлы. Для загрузки через терминал нужен [Hugging Face CLI](https://huggingface.co/docs/huggingface_hub/guides/cli).

## Обмен данными

File Setup передает исходный файл связанным нодам и использует папку результата в Output текущего экземпляра ComfyUI. Родительскую папку можно изменить через поле **Каталог результатов** в расширенных настройках File Setup.

По умолчанию в имя папки и основных файлов входят дата и время в формате `ГГГГ-ММ-ДД ЧЧ-ММ`, затем **Имя результата**, если оно задано. Если **Дата и время в имени** отключены, а поле имени пустое, используется имя исходного файла без расширения. При совпадении имени папки новые файлы добавляются в нее. Если имя файла занято, к новому файлу добавляется номер перед расширением, например `Встреча (2).txt`.

Промежуточные файлы хранятся в `Data/` по этапам. Экспортируемые файлы записываются в корень папки запуска. Готовая разметка и распознавание повторно используются после проверки исходника, модели и настроек. Имена, полученные при поиске по голосам, остаются кандидатами до подтверждения карты вручную.

## Эксплуатация

### Установка

Разместить пакет по [общей инструкции](../README.md#установка). В PowerShell перейти в рабочий корень из конфигурации и выполнить команды ниже. Пример использует CPU сборку PyTorch и просит указать путь к установленному Python 3.14.

```powershell
$speechRoot = (Get-Location).Path
$speechPython = Read-Host 'Полный путь к python.exe версии 3.14'
$whisper = Join-Path $speechRoot 'Apps\FasterWhisper\.venv'
$analysis = Join-Path $speechRoot 'Apps\SpeakerAnalysis\.venv'
& $speechPython -m venv $whisper
& $speechPython -m venv $analysis
& "$whisper\Scripts\python.exe" -m pip install faster-whisper==1.2.1 ctranslate2==4.8.1
& "$analysis\Scripts\python.exe" -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
& "$analysis\Scripts\python.exe" -m pip install torchcodec==0.11.1 pyannote.audio==4.0.7 speechbrain==1.1.0
```

### Настройка

Скопировать шаблон в рабочий файл конфигурации. Если окружения находятся в `Apps/FasterWhisper/.venv` и `Apps/SpeakerAnalysis/.venv` относительно рабочего корня, оставить пути к Python в `runtime` пустыми. Для других окружений указать пути к их исполняемым файлам Python. Пустые пути к скриптам обработки используют скрипты из пакета.

Сохранить выбранные модели в отдельные папки внутри каталога моделей ComfyUI, например в `speech/`, или указать свои каталоги в `paths.model_roots`. Подойдет полный снимок Hugging Face, полученный через `hf download <owner/repository> --revision <revision> --local-dir <каталог>`. В каждый каталог модели добавить `manifest.json`. Пример для Whisper Large V3.

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

Для остальных комплектов используются имена и семейства из таблицы. У Turbo указывается только `transcribe`. Для Community 1 в список весов входят `embedding/pytorch_model.bin` и `segmentation/pytorch_model.bin`. Для ECAPA указывается `embedding_model.ckpt`. Пути в списке относительны каталогу модели. После перезапуска ComfyUI проверить, что нужная модель доступна в соответствующей ноде.

### Использование

В [workflows/](workflows/) восемь готовых процессов. В режиме приложения параметры находятся в боковой панели. Подсказка открывается при наведении на значок рядом с полем, а нажатие закрепляет ее.

Поля исходных файлов, готовых результатов, отпечатков, карт имен и каталогов принимают полный путь или путь от рабочего корня, например `input/запись.wav`.

Перед запуском указать исходный файл и выбрать установленные модели, если они требуются. В стандартной **Расшифровке** включены TXT, JSON и SRT, VTT доступен отдельно. **Транскрипт по карте** использует готовую разметку и карту имен. **Эталоны голосов** и **Карта имен** доступны в расширенных входных данных соответствующих нод.

Карточка **Результат** показывает папку запуска, файлы и краткую сводку, если она предусмотрена нодой. В графе карточка находится в ноде и показывает ее файлы. В приложении итоговые файлы собраны в общей карточке. **Поиск голосов** и **Транскрипт с именами** выводят отдельными группами JSON кандидатов и исходную разметку, чтобы их можно было использовать повторно.

Под карточкой приложение выводит штатный предпросмотр ComfyUI, когда нода его предоставляет. Файлы из Output доступны по ссылкам для скачивания. Для файлов вне Output отображаются имена и путь к папке запуска. При локальном подключении к ComfyUI на Windows кнопка **Открыть папку** открывает каталог результата текущего сеанса.

### Проверка

Из корня репозитория с Python экземпляра ComfyUI и корнем ComfyUI в `PYTHONPATH`.

```powershell
& '<Python экземпляра ComfyUI>' -B -m unittest discover -s Speech\Tests
```
