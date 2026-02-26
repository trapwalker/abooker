# abooker

Сервис для публикации аудиокниг с торрентов в виде RSS-фидов. Скачивает файлы лениво — только когда слушатель запрашивает трек — и автоматически управляет кешем.

## Возможности

- **RSS-фид** совместимый с любым подкаст-клиентом (iTunes/Google Play extensions)
- **Lazy loading** — файл скачивается в момент первого запроса, HTTP-соединение держится открытым
- **Умный кеш** — прогрев следующих треков после прослушивания, вытеснение неактивных файлов
- **Telegram-бот** — отправьте magnet-ссылку, `.torrent`-файл или URL, бот создаст публикацию
- **Поиск** через Jackett (`/search <запрос>`) с кнопкой «Опубликовать»
- **CLI** для генерации RSS из локальной папки (исходный режим работы)

## Архитектура

```
abooker-web   — FastAPI: /publications  /rss/{slug}  /file/{slug}/{path}
abooker-bot   — aiogram 3: приём торрентов, /list /status /search
qbittorrent   — торрент-клиент (lscr.io/linuxserver/qbittorrent)
jackett       — поиск по трекерам  (lscr.io/linuxserver/jackett)
```

Данные хранятся в `/data/{slug}/`: `publication.json`, `playlist.rss`, `files/`.
SQLite (`abooker.db`) хранит статусы файлов, клиентов и события скачивания.
При потере БД — восстановление из `publication.json` на старте.

## Быстрый старт (локальная разработка)

```bash
# Зависимости
make install

# Веб-сервер с hot-reload (порт 8000)
make dev-web

# Telegram-бот
make dev-bot

# Оба сервиса параллельно
make dev
```

Скопируйте `.env.example` → `.env` и заполните переменные:

```bash
cp .env.example .env
```

## Docker Compose (production)

```bash
cp .env.example .env
# отредактировать .env

docker compose up -d
```

Сервисы: `abooker-web` (порт 8000), `abooker-bot`, `qbittorrent` (WebUI :8080), `jackett` (:9117).

## Развёртывание на VDS (без Docker)

Подробная инструкция: [`deploy/DEPLOY.md`](deploy/DEPLOY.md)

Краткий сценарий:

```bash
# Установка на VDS (из локальной машины)
make vds-deploy VDS_HOST=1.2.3.4 VDS_USER=abooker

# Управление сервисами
make vds-up        # запустить
make vds-down      # остановить
make vds-restart   # перезапустить
make vds-logs      # логи в реальном времени
```

nginx-конфиг для `sites-enabled`: [`deploy/nginx/abooker.conf`](deploy/nginx/abooker.conf)
systemd-юниты: [`deploy/abooker-web.service`](deploy/abooker-web.service), [`deploy/abooker-bot.service`](deploy/abooker-bot.service)

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
  cli.py / rss.py / settings.py / files.py  — CLI-ядро
  core/        — config, ORM-модели, БД, slug
  torrent/     — qbittorrent-api wrapper, create_publication()
  cache/       — warmup, eviction, client tracking
  web/         — FastAPI app + routes
  bot/         — aiogram 3 handlers, keyboards
  search/      — Jackett API
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DATA_DIR` | `/data` | Каталог хранения публикаций |
| `QB_HOST` | `qbittorrent` | Хост qBittorrent |
| `QB_PORT` | `8080` | Порт WebUI qBittorrent |
| `QB_USER` / `QB_PASS` | `admin` / `adminadmin` | Авторизация qBittorrent |
| `JACKETT_URL` | `http://jackett:9117` | URL Jackett |
| `JACKETT_KEY` | — | API-ключ Jackett |
| `WARMUP_COUNT` | `2` | Первых N файлов всегда в кеше |
| `WARMUP_DEPTH` | `3` | Кешировать N следующих треков за слушателем |
| `CLIENT_TTL_DAYS` | `14` | Дней без активности до исключения клиента из расчёта кеша |
| `BOT_TOKEN` | — | Токен Telegram-бота (от @BotFather) |
| `WEB_BASE_URL` | `http://localhost:8000` | Публичный URL сервера |
