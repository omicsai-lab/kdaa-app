#!/bin/sh
set -eu
python -m kdaa_api.startup
python -m alembic upgrade head
exec python -m uvicorn kdaa_api.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
