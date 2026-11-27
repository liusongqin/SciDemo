#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

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

echo "Local model is managed separately; start it manually when needed."

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
