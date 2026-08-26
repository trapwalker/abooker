# abooker

Сервис для поиска, скачивания с торрентов и экономного self-хостинга
аудиокниг. Находишь книгу через Telegram-бота, бот добавляет её в твою
коллекцию и выдаёт RSS-фид, подписываешься на него в любом подкаст-клиенте —
и слушаешь. Главы скачиваются лениво (только когда реально нужны) и
самостоятельно вычищаются из кеша, когда становится мало места.

## Возможности

- **Поиск торрентов** через Jackett — по умолчанию rutracker.org, список
  трекеров расширяется через настройки Jackett, без правок кода abooker.
- **Коллекция** — из результатов поиска или присланной magnet/.torrent/URL
  ссылки бот создаёт публикацию и добавляет её пользователю.
- **RSS-фид** на каждую книгу, совместимый с любым подкаст-клиентом
  (iTunes/Google Play extensions); порядок глав вычисляется по номеру
  главы в имени файла, а не по алфавиту (см. `files.chapter_sort_key`).
- **Постраничный список глав в боте** — статус каждой (⬜/⏳ с оценкой
  времени/✅), кнопки «🔥 прогреть» и «🗑 удалить» для каждой главы отдельно.
- **Ленивая раздача** — HTTP-сервер по ссылке из RSS отдаёт файл, если он в
  кеше; если нет — сразу отвечает 503 и ставит главу (плюс несколько
  следующих) на закачку торрент-клиентом, не удерживая соединение.
- **Прогрев наперёд** — после отдачи главы N автоматически подкачиваются
  N+1..N+WARMUP_DEPTH.
- **Вытеснение по месту на диске** — как только свободного места становится
  меньше `MIN_FREE_DISK_GB`, глобально (across всех книг) удаляются главы с
  самой старой датой последнего обращения — независимо от того, чья это
  книга и какая у неё сейчас «зона прогрева».
- **CLI** для генерации RSS из локальной папки (исходный режим работы).

## Архитектура

```
abooker-web   — FastAPI: /publications  /rss/{slug}  /file/{slug}/{path}
abooker-bot   — aiogram 3: приём торрентов, /list /status /search, чаты по главам
qbittorrent   — торрент-клиент (lscr.io/linuxserver/qbittorrent)
jackett       — поиск по трекерам, список трекеров настраивается в самом Jackett
```

Данные хранятся в `DATA_DIR/{slug}/`: `publication.json` (для восстановления
после потери БД), `playlist.rss`, `files/` (символические ссылки на файлы,
скачанные qBittorrent — без дублирования на диске). SQLite (`abooker.db`)
хранит статусы файлов, клиентов и события скачивания.

### Почему 503, а не «подержать соединение открытым»

Раньше `/file/...` при промахе кеша ждал скачивания прямо в хендлере. Сейчас
он сразу отвечает `503` с телом `{"status": "downloading", "eta_seconds": …}`
и `Retry-After`, а закачку (текущей главы и нескольких следующих) ставит в
фоне. Подкачанные в фоне файлы переводятся в `cached` отдельной задачей
(`cache.manager.periodic_finalize`, тикает раз в 15 сек) — раньше это делал
только блокирующий хендлер, и без него фоновый прогрев никогда бы не
«материализовался» в раздаваемый кеш.

### Технический слаг для сортировки глав

Торренты именуют файлы как попало: `Глава 10.mp3` перед `Глава 2.mp3` по
алфавиту, номер может стоять и после названия главы. `chapter_sort_key()`
(`files.py`) вытаскивает наиболее вероятный номер главы (с приоритетом на
ключевые слова «глава/часть/track/chapter», игнорируя цифры из `128kbps` и
похожего), переставляет его в начало ключа с ведущими нулями — и уже потом
сортирует строкой.

## Быстрый старт (локальная разработка)

```bash
make install
cp .env.example .env   # заполнить BOT_TOKEN, JACKETT_KEY и т.д.

make dev-web   # веб-сервер с hot-reload, порт 8000
make dev-bot   # Telegram-бот
make dev       # оба сразу
```

## Docker Compose (всё в контейнерах, для локального теста)

```bash
cp .env.example .env
docker compose up -d
```

`abooker-web` (:8000), `abooker-bot`, `qbittorrent` (WebUI :8080), `jackett` (:9117).

## Развёртывание на VDS

Боевая инстанция — `sl2` / `https://ab.sublayers.net`. `abooker-web` и
`abooker-bot` там работают нативно через systemd (не в Docker — на маленькой
общей машине это ощутимо экономнее), `qbittorrent`+`jackett` — в Docker
(`docker-compose.vds.yml`), раздача идёт через уже существующий на машине
общий nginx-контейнер. Подробности и обоснование архитектуры —
[`deploy/DEPLOY.md`](deploy/DEPLOY.md).

```bash
make vds-deploy      # код + зависимости + рестарт systemd
make vds-up/down/restart/logs/status
make vds-docker-up/down/logs   # qbittorrent + jackett
```

## CLI (генерация RSS из локальной папки)

```bash
uv run abooker -d path/to/audiobook_folder \
    --url=http://my_host/path/to/abooks/ \
    --title="Война и мир" \
    --author="Лев Толстой"
```

| Опция | Описание |
|---|---|
| `-d / --dir` | Путь к папке с аудиофайлами (обязательно) |
| `-u / --url` | Базовый URL для HTTP-раздачи |
| `--rss` | Имя выходного файла (по умолчанию `playlist.rss`) |
| `--title`, `--author`, `--description`, `--image`, `--lang`, `--link` | Метаданные |
| `-S` | Сохранить настройки в `.abooker` родительской папки |
| `--no-local-settings` | Не загружать настройки родительской папки |
| `--verbose` | Отладочный вывод |

## Структура пакета

```
src/abooker/
  cli.py / rss.py / settings.py / files.py  — CLI-ядро + chapter_sort_key()
  format.py    — fmt_bytes/fmt_duration (используются ботом)
  core/        — config, ORM-модели, БД (+ лёгкие ALTER TABLE миграции), slug
  torrent/     — qbittorrent-api wrapper, create_publication()
  cache/       — warmup, eviction, дисковая квота, финализация фоновых закачек
  web/         — FastAPI app + routes (/publications, /rss, /file)
  bot/         — aiogram 3 handlers (search/list/status/auth/chapters), keyboards
  search/      — Jackett API
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DATA_DIR` | `/data` | Каталог хранения публикаций |
| `QB_HOST` / `QB_PORT` | `qbittorrent` / `8080` | qBittorrent WebUI |
| `QB_USER` / `QB_PASS` | `admin` / `adminadmin` | Авторизация qBittorrent |
| `JACKETT_URL` / `JACKETT_KEY` | `http://jackett:9117` / — | Jackett |
| `WARMUP_COUNT` | `2` | Первых N файлов всегда в кеше |
| `WARMUP_DEPTH` | `3` | Кешировать N следующих глав за слушателем |
| `CLIENT_TTL_DAYS` | `14` | Дней без активности до исключения клиента из расчёта прогрева |
| `MIN_FREE_DISK_GB` | `3` | Порог свободного места — ниже него глобально вытесняются самые старые главы |
| `NGINX_ACCEL` | `false` | Отдавать файлы через `X-Accel-Redirect` (нужен nginx с `location /_internal_files/`) |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8000` | На чём слушает uvicorn |
| `BOT_TOKEN` / `BOT_ADMIN_IDS` | — | Telegram-бот и список ID администраторов |
| `WEB_BASE_URL` | `http://localhost:8000` | Публичный URL сервера (используется в ссылках RSS) |
| `MAX_FILE_SIZE_GB` / `MIN_AUDIO_RATIO` | `5` / `0.5` | Фильтр раздач при добавлении книги |
