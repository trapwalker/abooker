.PHONY: help install install-all dev-web dev-bot dev \
        vds-up vds-down vds-restart vds-deploy vds-logs vds-status \
        vds-docker-up vds-docker-down vds-docker-logs \
        log_bot_remote log_srv_remote \
        docker-up docker-down docker-logs

# ─── Настройки ────────────────────────────────────────────────────────────────

# SSH alias from ~/.ssh/config.d/ — see CLAUDE.md / project memory.
VDS_HOST  ?= sl2
VDS_DIR   ?= /home/svp/abooker
VENV      := .venv
PYTHON    := $(VENV)/bin/python
UV        := uv

# `srv=<ssh-alias>` override for the log_*_remote targets, e.g.
# `make log_bot_remote srv=sl2`. Defaults to VDS_HOST so it also works bare.
srv ?= $(VDS_HOST)

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
	@echo "    make log_bot_remote srv=sl2  — стрим журнала abooker-bot (не докер, systemd)"
	@echo "    make log_srv_remote srv=sl2  — стрим журнала abooker-web (не докер, systemd)"
	@echo ""
	@echo "  Переменные: VDS_HOST=$(VDS_HOST)  VDS_DIR=$(VDS_DIR)  srv=$(srv)"

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

# Веб-сервер с hot-reload (uvicorn --reload). Этот путь не проходит через
# abooker.web.main:run()/setup_logging(), поэтому формат логов задаём тем же
# logging.yaml явным флагом — иначе тут был бы формат uvicorn по умолчанию.
dev-web:
	$(UV) run uvicorn abooker.web.main:app --host 0.0.0.0 --port 8000 --reload --log-config src/abooker/logging.yaml

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
	# `ssh host "cmd"` runs a non-interactive, non-login shell that doesn't
	# source .bashrc/.profile, so uv (installed under ~/.local/bin) isn't on
	# PATH there even though it is in an interactive session — hence the
	# explicit PATH prefix below.
	ssh $(VDS_HOST) " \
	    export PATH=\$$HOME/.local/bin:\$$PATH && \
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

# ─── Удалённые логи по имени сервиса ──────────────────────────────────────────
# abooker-bot/abooker-web on the VDS are native systemd services, not Docker
# containers (see deploy/DEPLOY.md) — this streams `journalctl -u <unit> -f`
# over SSH, which is the equivalent of `docker logs -f` for them.
#
# -o cat drops journald's own "Aug 28 01:48:14 abooker-bot[1271381]:" prefix.
# Every line we emit already carries its own ISO timestamp (logging.yaml) —
# without -o cat you'd see that timestamp *and* journald's own non-ISO one
# on every line, which is confusing, not just redundant.

log_bot_remote:
	ssh $(srv) "sudo journalctl -u abooker-bot -f -o cat"

log_srv_remote:
	ssh $(srv) "sudo journalctl -u abooker-web -f -o cat"
