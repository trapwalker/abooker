# Развёртывание abooker на VDS

## Требования

- Ubuntu 22.04+ (или аналогичный Debian-like дистрибутив)
- Python 3.12+
- nginx
- uv (`pip install uv` или `curl -Lf https://astral.sh/uv/install.sh | sh`)
- qBittorrent и Jackett (через Docker или нативно)

---

## 1. Создание пользователя и директорий

```bash
sudo useradd -r -m -d /opt/abooker -s /usr/sbin/nologin abooker
sudo mkdir -p /data/abooker
sudo chown abooker:abooker /data/abooker
```

---

## 2. Установка приложения

```bash
# Клонируем репозиторий
sudo git clone https://github.com/youruser/abooker.git /opt/abooker
sudo chown -R abooker:abooker /opt/abooker

# Устанавливаем зависимости в виртуальное окружение
sudo -u abooker bash -c "cd /opt/abooker && uv venv .venv && uv pip install --python .venv '.[all]'"
```

---

## 3. Файл окружения

```bash
sudo cp /opt/abooker/.env.example /opt/abooker/.env
sudo chown abooker:abooker /opt/abooker/.env
sudo chmod 600 /opt/abooker/.env
sudo nano /opt/abooker/.env   # заполнить BOT_TOKEN, WEB_BASE_URL и др.
```

Минимум, что нужно прописать:

```
DATA_DIR=/data/abooker
WEB_BASE_URL=https://your-domain.com
BOT_TOKEN=123456789:AABBCCddEEff...
QB_HOST=localhost           # или IP qBittorrent
QB_PASS=your-qbt-password
```

---

## 4. systemd — установка и автозапуск

```bash
# Копируем юниты
sudo cp /opt/abooker/deploy/abooker-web.service /etc/systemd/system/
sudo cp /opt/abooker/deploy/abooker-bot.service /etc/systemd/system/

# Перечитываем конфигурацию systemd
sudo systemctl daemon-reload

# Включаем автозапуск при старте системы
sudo systemctl enable abooker-web abooker-bot

# Запускаем прямо сейчас
sudo systemctl start abooker-web abooker-bot

# Проверяем статус
sudo systemctl status abooker-web abooker-bot
```

### Просмотр логов

```bash
sudo journalctl -u abooker-web -f
sudo journalctl -u abooker-bot -f
```

---

## 5. nginx

```bash
# Копируем конфиг и правим домен
sudo cp /opt/abooker/deploy/nginx/abooker.conf /etc/nginx/sites-available/abooker.conf
sudo nano /etc/nginx/sites-available/abooker.conf   # вписать server_name

# Активируем сайт
sudo ln -s /etc/nginx/sites-available/abooker.conf /etc/nginx/sites-enabled/abooker.conf

# Проверяем синтаксис и перезагружаем
sudo nginx -t && sudo systemctl reload nginx
```

### SSL через Certbot (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com

# Раскомментируйте HTTPS-блок в abooker.conf после получения сертификата
sudo systemctl reload nginx
```

---

## 6. Обновление приложения

```bash
sudo -u abooker bash -c "cd /opt/abooker && git pull && uv pip install --python .venv '.[all]'"
sudo systemctl restart abooker-web abooker-bot
```

---

## Управление сервисами

| Действие          | Команда                                    |
|-------------------|--------------------------------------------|
| Запустить оба     | `sudo systemctl start abooker-web abooker-bot` |
| Остановить оба    | `sudo systemctl stop abooker-web abooker-bot`  |
| Перезапустить     | `sudo systemctl restart abooker-web abooker-bot` |
| Логи веб          | `sudo journalctl -u abooker-web -f`        |
| Логи бота         | `sudo journalctl -u abooker-bot -f`        |
| Отключить автозапуск | `sudo systemctl disable abooker-web abooker-bot` |
