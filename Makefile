.PHONY: help install install-all dev-web dev-bot dev vds-up vds-down vds-restart vds-logs

# ─── Настройки ────────────────────────────────────────────────────────────────

VDS_USER  ?= abooker
VDS_HOST  ?= your-vds-hostname
VDS_DIR   ?= /opt/abooker
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
	@echo "  VDS (быстрое управление):"
	@echo "    make vds-up      — запустить сервисы на VDS"
	@echo "    make vds-down    — остановить сервисы на VDS"
	@echo "    make vds-restart — перезапустить сервисы на VDS"
	@echo "    make vds-deploy  — залить код на VDS и перезапустить"
	@echo "    make vds-logs    — показать логи с VDS (Ctrl-C для выхода)"
	@echo ""
	@echo "  Переменные:"
	@echo "    VDS_USER=$(VDS_USER)  VDS_HOST=$(VDS_HOST)  VDS_DIR=$(VDS_DIR)"

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
	ssh $(VDS_USER)@$(VDS_HOST) "sudo systemctl start abooker-web abooker-bot"

vds-down:
	ssh $(VDS_USER)@$(VDS_HOST) "sudo systemctl stop abooker-web abooker-bot"

vds-restart:
	ssh $(VDS_USER)@$(VDS_HOST) "sudo systemctl restart abooker-web abooker-bot"

# Залить текущий код и перезапустить
vds-deploy:
	rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
	      ./ $(VDS_USER)@$(VDS_HOST):$(VDS_DIR)/
	ssh $(VDS_USER)@$(VDS_HOST) " \
	    cd $(VDS_DIR) && \
	    uv pip install --python .venv '.[all]' && \
	    sudo systemctl restart abooker-web abooker-bot \
	"

# Логи обоих сервисов с VDS в реальном времени
vds-logs:
	ssh $(VDS_USER)@$(VDS_HOST) "sudo journalctl -u abooker-web -u abooker-bot -f --no-hostname"
