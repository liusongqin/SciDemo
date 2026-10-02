#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

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

if [[ "${SSH_REVERSE_TUNNEL_ENABLED:-false}" == "true" ]]; then
  : "${SSH_REVERSE_TUNNEL_HOST:?Set SSH_REVERSE_TUNNEL_HOST in .env}"
  : "${SSH_REVERSE_TUNNEL_USER:?Set SSH_REVERSE_TUNNEL_USER in .env}"

  remote_bind_host="${SSH_REVERSE_TUNNEL_REMOTE_BIND_HOST:-0.0.0.0}"
  remote_port="${SSH_REVERSE_TUNNEL_REMOTE_PORT:-5173}"
  local_host="${SSH_REVERSE_TUNNEL_LOCAL_HOST:-127.0.0.1}"
  local_port="${SSH_REVERSE_TUNNEL_LOCAL_PORT:-5173}"

  ssh -NT \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=accept-new \
    -R "${remote_bind_host}:${remote_port}:${local_host}:${local_port}" \
    "${SSH_REVERSE_TUNNEL_USER}@${SSH_REVERSE_TUNNEL_HOST}" &
  children+=("$!")
  echo "SSH reverse tunnel: http://${SSH_REVERSE_TUNNEL_HOST}:${remote_port}"
fi

echo "SciDemo local: http://127.0.0.1:5173"
echo "SciDemo LAN:   http://<server-lan-ip>:5173"
wait -n "${children[@]}"
