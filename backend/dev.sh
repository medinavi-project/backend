#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${APP_PORT:-8000}" --reload --reload-dir app --reload-delay 0.5
