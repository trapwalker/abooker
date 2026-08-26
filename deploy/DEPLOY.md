# Развёртывание abooker на VDS

Актуальное боевое окружение: VDS `sl2` (см. `~/.ssh/config.d/`), домен
`https://ab.sublayers.net`. Ниже — как оно устроено и как его повторить
с нуля на похожей машине.

## Архитектура на VDS

Машина маленькая (1 vCPU, ~1.8 ГБ RAM) и **общая** — на ней уже крутятся чужие
боевые сайты в Docker (тот же `nginx`-контейнер проксирует несколько доменов).
Поэтому:

- `abooker-web` и `abooker-bot` работают **нативно** через `uv venv` + systemd,
  не в Docker — это ощутимо экономит память по сравнению с ещё двумя
  контейнерами.
- `qbittorrent` и `jackett` — в Docker (`docker-compose.vds.yml`), с лимитами
  памяти (`mem_limit`) и WebUI, слушающим только `127.0.0.1`.
- Раздача `/rss/*` и `/publications` идёт через уже существующий общий
  `nginx`-контейнер (новый vhost-файл, без отдельного nginx). Сам аудио-байт
  трафик по возможности отдаёт nginx напрямую (`X-Accel-Redirect`), а не
  процесс FastAPI — на 1 CPU это заметно.

```
Internet ── shared nginx (docker, :80/:443) ──┬─ другие сайты (rd., trillink., shop)
                                                └─ ab.sublayers.net
                                                     ├─ proxy_pass → 127.0.0.1:8010 (abooker-web, systemd)
                                                     └─ internal alias → DATA_DIR (аудиофайлы, X-Accel-Redirect)

abooker-bot (systemd, aiogram, long polling) ──┐
abooker-web (systemd, uvicorn :8010) ──────────┼── DATA_DIR (/home/svp/abooker/data)
qbittorrent (docker, WebUI 127.0.0.1:8080) ────┤        ↑ save_path бинд-маунтится
jackett (docker, WebUI 127.0.0.1:9117) ────────┘        по тому же абсолютному пути,
                                                          что и на хосте — файлы можно
                                                          симлинкать, а не копировать.
```

## 1. Каталоги и код

```bash
ssh sl2 "mkdir -p /home/svp/abooker/{data,qbt-config,jackett-config}"
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' \
      ./ sl2:/home/svp/abooker/
```

(В точности то же самое делает `make vds-deploy`.)

## 2. `.env`

Копируется на сервер **вручную** (никогда не через git — `.env` в `.gitignore`),
права `600`. Ключевые отличия боевого `.env` от локального:

```
DATA_DIR=/home/svp/abooker/data
QB_HOST=127.0.0.1                 # docker публикует WebUI только на localhost
JACKETT_URL=http://127.0.0.1:9117
WEB_BASE_URL=https://ab.sublayers.net
WEB_HOST=0.0.0.0                  # см. примечание про host.docker.internal ниже
WEB_PORT=8010                     # 8000 занят соседним проектом (rd_app)
NGINX_ACCEL=true                  # отдавать файлы через X-Accel-Redirect
MIN_FREE_DISK_GB=3                # диск общий с другими проектами, держим запас
```

**Важно про `WEB_HOST`**: нативный процесс не в Docker, а общий nginx — в
Docker, поэтому он достаёт до него через `host.docker.internal` (см. ниже).
Трафик от `host.docker.internal` приходит на хост как обычный TCP-пакет с
IP из подсети docker-моста nginx — если `abooker-web` слушает `127.0.0.1`,
такие пакеты до него не доходят (loopback принимает только с самого
loopback-интерфейса). Поэтому `WEB_HOST=0.0.0.0`, а наружу порт закрыт
файрволом:

```bash
docker network inspect nginx_default --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
# → 172.19.0.0/16 (подсеть может отличаться — проверить на месте)
sudo ufw allow from 172.19.0.0/16 to any port 8010 proto tcp \
     comment 'abooker-web from shared nginx container'
```

`ufw` по умолчанию (`DROP` на `INPUT`) блокирует порт `8010` для всех, кроме
этой подсети — снаружи `curl http://<VDS-IP>:8010/` не отвечает, только
через `https://ab.sublayers.net/` (проверено).

## 3. qBittorrent + Jackett (Docker)

```bash
cd /home/svp/abooker
DATA_DIR=/home/svp/abooker/data docker compose -f docker-compose.vds.yml --env-file .env up -d
```

Оба сервиса слушают `127.0.0.1` — снаружи недоступны. Чтобы открыть WebUI
для ручной проверки: `ssh -L 8080:localhost:8080 -L 9117:localhost:9117 sl2`.

`qbittorrent`: пароль WebUI при первом старте генерируется автослучайно
(смотреть `docker logs abooker-qbittorrent`, строка "temporary password").
Один раз при первом провижининге сменить на `QB_PASS` из `.env`:

```bash
docker logs abooker-qbittorrent 2>&1 | grep -i temporary   # разовый пароль
COOKIE=/tmp/qbt_cookie.txt
curl -s -c "$COOKIE" -X POST http://127.0.0.1:8080/api/v2/auth/login \
     --data-urlencode "username=admin" --data-urlencode "password=<temp>"
curl -s -b "$COOKIE" -X POST http://127.0.0.1:8080/api/v2/app/setPreferences \
     --data-urlencode "json={\"web_ui_password\": \"$QB_PASS\"}"
```

`jackett`: `scripts/jackett-set-apikey.sh` (custom-cont-init.d) выставляет
API-ключ из `JACKETT_KEY` при каждом старте контейнера, так что ключ
никогда не «уезжает» от значения в `.env`.

### Индексатор rutracker.org и блокировка трекеров на sl2

Через API Jackett (`GET/POST /api/v2.0/indexers/rutracker/config`, сессионная
кука из `/UI/Login`, не `apikey` — тот только для торзнаб-поиска) с
логином/паролем аккаунта rutracker. Через настройки Jackett можно так же
добавить любой другой индексатор — это и есть «расширяемый список
источников поиска» из требований: правка не в коде abooker, а в Jackett.

**На sl2 это не работает: исходящие TLS-соединения к трекерам блокируются
на уровне сети/провайдера.** Проверено (`curl` с VDS): `rutracker.org`,
`nnmclub.to`, `audiobookbay.lu`, `1337x.to`, `thepiratebay.org` — все
таймаутят на TLS handshake, при этом `github.com`/`wikipedia.org` отвечают
мгновенно. Это не проблема конфигурации Jackett/apikey — сам `.NET`-клиент
Jackett получает `SSL connection could not be established … unexpected EOF`
при попытке войти на rutracker. Jackett поддерживает исходящий прокси
(`Settings → Proxy` в его UI) — если появится рабочий SOCKS5/HTTP-прокси с
доступом к трекерам, это единственный способ включить поиск на этой машине.
До тех пор ручное добавление книг по magnet/.torrent-ссылке в боте работает
нормально — блокировка касается только поиска через Jackett.

## 4. Python-окружение и systemd

```bash
ssh sl2 "cd /home/svp/abooker && uv venv .venv && uv pip install --python .venv -e '.[all]'"

sudo cp /home/svp/abooker/deploy/abooker-web.service /etc/systemd/system/
sudo cp /home/svp/abooker/deploy/abooker-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now abooker-web abooker-bot
sudo systemctl status abooker-web abooker-bot
```

Логи: `sudo journalctl -u abooker-web -u abooker-bot -f`.

## 5. nginx (общий контейнер)

Конфиги общего nginx лежат на хосте в `/home/svp/nginx/sites/` и
монтируются в контейнер `nginx-nginx-1` (см. `/home/svp/nginx/docker-compose.yml`).
Он поднят через `docker compose` в `/home/svp/nginx/`, без `mem_limit`.

1. Скопировать `deploy/nginx/ab.sublayers.net.conf` в `/home/svp/nginx/sites/`,
   подставив реальный `DATA_DIR` вместо `DATA_DIR_PLACEHOLDER`.
2. В `/home/svp/nginx/docker-compose.yml` (сначала сделать бэкап файла!)
   у сервиса `nginx` добавить:
   - `extra_hosts: ["host.docker.internal:host-gateway"]` — чтобы контейнер
     достучался до нативного процесса на хосте;
   - volume `/home/svp/abooker/data:/home/svp/abooker/data:ro` — чтобы
     работал `location /_internal_files/` (X-Accel-Redirect отдаёт файлы
     напрямую из nginx, не через Python).
   Затем `docker compose up -d nginx` (именно `up`, не `restart` —
   `extra_hosts`/новый volume требуют пересоздания контейнera, не просто
   перезапуска).
3. **`WEB_HOST` должен быть `0.0.0.0`, не `127.0.0.1`** — трафик от
   `host.docker.internal` приходит с адресом из подсети docker-моста
   nginx, а не с loopback, и `127.0.0.1`-биндинг такие пакеты не примет.
   Порт наружу не открывается: `ufw allow from <подсеть-моста-nginx> to any
   port 8010` (см. раздел про переменные окружения выше), плюс сам процесс
   слушает нестандартный порт, известный только nginx.
4. **Сертификат ещё не выпущен** — сначала оставить только HTTP-блок
   (закомментировать `server { listen 443 ... }`), перезагрузить nginx,
   получить сертификат через уже работающий `certbot`-контейнер
   (`docker compose exec certbot certbot certonly --webroot -w /var/certbot -d ab.sublayers.net`),
   затем раскомментировать HTTPS-блок и перезагрузить nginx ещё раз.
5. Проверить, что остальные вхосты (`rd.`, `trillink.`, магазин) как и
   раньше отвечают — общий контейнер, ошибка в одном файле валит весь nginx.

```bash
docker compose -f /home/svp/nginx/docker-compose.yml exec nginx nginx -t
docker compose -f /home/svp/nginx/docker-compose.yml restart nginx
```

## Сетевые особенности sl2

- **`api.telegram.org` резолвится в IP, который на этой сети не отвечает**
  (`149.154.166.110` — таймаут), при этом другой IP того же Telegram
  (`149.154.167.220`) отвечает мгновенно и обслуживает реальный Bot API.
  Без обхода `abooker-bot` не мог даже пройти `getMe()` на старте и
  постоянно падал по `TelegramNetworkError` (systemd уходил в рестарт-луп).
  Фикс — закрепить рабочий IP в `/etc/hosts` на хосте:
  ```bash
  echo '149.154.167.220 api.telegram.org' | sudo tee -a /etc/hosts
  sudo systemctl restart abooker-bot
  ```
  Если Telegram сменит балансировку и этот IP перестанет отвечать,
  подобрать новый тем же способом (`curl --resolve api.telegram.org:443:<ip>
  https://api.telegram.org/`) и поправить `/etc/hosts`.
- Трекеры (см. раздел про rutracker выше) заблокированы иначе — по всей
  подсети/SNI, тем же трюком с одним IP не обходятся, там нужен прокси.

## 6. Обновление

```bash
make vds-deploy   # rsync кода + переустановка зависимостей + restart systemd
```

`docker-compose.vds.yml` (qbittorrent/jackett) обновлять руками —
не пересоздаётся при каждом деплое, чтобы не терять состояние торрентов.

## Управление

| Действие | Команда |
|---|---|
| web+bot: старт/стоп/рестарт | `make vds-up` / `vds-down` / `vds-restart` |
| web+bot: логи | `make vds-logs` |
| web+bot+диск: статус | `make vds-status` |
| qbt+jackett: старт/стоп/логи | `make vds-docker-up` / `vds-docker-down` / `vds-docker-logs` |
