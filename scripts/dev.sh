#!/usr/bin/env bash
set -euo pipefail
trap 'kill 0' EXIT
.venv/bin/uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000 &
npm --prefix frontend run dev &
wait
