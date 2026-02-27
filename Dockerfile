FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN uv pip install --system ".[all]"

ENV DATA_DIR=/data
VOLUME /data

# Entrypoint is set per-service in docker-compose.yml
