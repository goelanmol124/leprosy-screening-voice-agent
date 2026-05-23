#!/usr/bin/env bash
set -euo pipefail

if [ "${RAILWAY_SERVICE_NAME:-}" = "leprosy-agent" ]; then
  exec python agent.py start
fi

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:?}"
