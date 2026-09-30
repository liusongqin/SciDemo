#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

children=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${children[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if ! curl --silent --fail http://127.0.0.1:8001/v1/models >/dev/null 2>&1; then
  bash scripts/serve-model.sh &
  children+=("$!")
  echo "Waiting for Qwen3.5-9B on http://127.0.0.1:8001/v1 ..."
  for _ in $(seq 1 180); do
    if curl --silent --fail http://127.0.0.1:8001/v1/models >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "${children[0]}" 2>/dev/null; then
      echo "vLLM exited before becoming ready." >&2
      exit 1
    fi
    sleep 2
  done
  curl --silent --fail http://127.0.0.1:8001/v1/models >/dev/null || {
    echo "Timed out waiting for vLLM." >&2
    exit 1
  }
else
  echo "Using the vLLM server already running on port 8001."
fi

.venv/bin/uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000 &
children+=("$!")
npm --prefix frontend run dev &
children+=("$!")
echo "SciDemo local: http://127.0.0.1:5173"
echo "SciDemo LAN:   http://<server-lan-ip>:5173"
wait -n "${children[@]}"
