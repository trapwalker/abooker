.PHONY: help install install-all dev-web dev-bot dev \
        vds-up vds-down vds-restart vds-deploy vds-logs vds-status \
        vds-docker-up vds-docker-down vds-docker-logs \
        docker-up docker-down docker-logs

# ─── Настройки ────────────────────────────────────────────────────────────────

# SSH alias from ~/.ssh/config.d/ — see CLAUDE.md / project memory.
VDS_HOST  ?= sl2
VDS_DIR   ?= /home/svp/abooker
VENV      := .venv
PYTHON    := $(VENV)/bin/python
UV        := uv

# ─── Помощь ───────────────────────────────────────────────────────────────────

help:
	@echo "Доступные команды:"
	@echo ""
	@echo "  Локальная разработка:"
	@echo "    make install     — установить зависимости (базовые + [all])"
	@echo "    make dev-web     — запустить веб-сервер с hot-reload"
	@echo "    make dev-bot     — запустить Telegram-бота"
	@echo "    make dev         — запустить веб + бота параллельно (tmux/screen не нужен)"
	@echo ""
	@echo "  Локальный docker compose (web+bot+qbt+jackett всё в контейнерах):"
	@echo "    make docker-up / docker-down / docker-logs"
	@echo ""
	@echo "  VDS ($(VDS_HOST):$(VDS_DIR)) — web/bot нативно через systemd,"
	@echo "  qbittorrent+jackett в docker compose (docker-compose.vds.yml):"
	@echo "    make vds-deploy      — залить код, поставить зависимости, перезапустить systemd"
	@echo "    make vds-up/down/restart — управление abooker-web/abooker-bot"
	@echo "    make vds-logs        — журнал systemd в реальном времени"
	@echo "    make vds-status      — systemctl status + свободное место на диске"
	@echo "    make vds-docker-up/down/logs — qbittorrent+jackett"
	@echo ""
	@echo "  Переменные: VDS_HOST=$(VDS_HOST)  VDS_DIR=$(VDS_DIR)"

# ─── Docker ───────────────────────────────────────────────────────────────────

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

# ─── Установка ────────────────────────────────────────────────────────────────

install:
	$(UV) venv $(VENV)
	$(UV) pip install --python $(VENV) -e ".[all]"

# ─── Локальная разработка ─────────────────────────────────────────────────────

# Веб-сервер с hot-reload (uvicorn --reload)
dev-web:
	$(UV) run uvicorn abooker.web.main:app --host 0.0.0.0 --port 8000 --reload

# Telegram-бот
dev-bot:
	$(UV) run abooker-bot

# Оба сервиса параллельно (используется встроенный make -j)
dev:
	@echo "Запускаем web + bot параллельно. Остановка: Ctrl-C"
	$(MAKE) -j2 dev-web dev-bot

# ─── VDS — быстрое управление через SSH ──────────────────────────────────────

vds-up:
	ssh $(VDS_HOST) "sudo systemctl start abooker-web abooker-bot"

vds-down:
	ssh $(VDS_HOST) "sudo systemctl stop abooker-web abooker-bot"

vds-restart:
	ssh $(VDS_HOST) "sudo systemctl restart abooker-web abooker-bot"

# Залить текущий код (без .env — он не в репозитории и не трогается) и перезапустить.
vds-deploy:
	rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' \
	      ./ $(VDS_HOST):$(VDS_DIR)/
	ssh $(VDS_HOST) " \
	    cd $(VDS_DIR) && \
	    uv venv -q .venv --allow-existing 2>/dev/null; \
	    uv pip install --python .venv -e '.[all]' && \
	    sudo systemctl restart abooker-web abooker-bot \
	"

# Логи обоих сервисов с VDS в реальном времени
vds-logs:
	ssh $(VDS_HOST) "sudo journalctl -u abooker-web -u abooker-bot -f --no-hostname"

vds-status:
	ssh $(VDS_HOST) "systemctl --no-pager status abooker-web abooker-bot; echo; df -h /; free -h"

# ─── VDS — qbittorrent + jackett (docker compose) ────────────────────────────

vds-docker-up:
	ssh $(VDS_HOST) "cd $(VDS_DIR) && DATA_DIR=$(VDS_DIR)/data docker compose -f docker-compose.vds.yml --env-file .env up -d"

vds-docker-down:
	ssh $(VDS_HOST) "cd $(VDS_DIR) && docker compose -f docker-compose.vds.yml down"

vds-docker-logs:
	ssh $(VDS_HOST) "cd $(VDS_DIR) && docker compose -f docker-compose.vds.yml logs -f"
