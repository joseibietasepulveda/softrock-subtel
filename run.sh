#!/usr/bin/env bash
# Arranque local: ./run.sh → http://localhost:8000. Configuración: README.md.
# Requiere Postgres: `docker compose up -d db` o DATABASE_URL apuntando a uno existente.
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
: "${DATABASE_URL:?Configure DATABASE_URL en .env o en el entorno}"
if [ ! -d .venv ]; then
  (command -v uv >/dev/null && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt) \
  || (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
fi
exec .venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
