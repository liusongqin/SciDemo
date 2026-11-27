#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Keep the original development/SSH settings intact; production overrides live separately.
set -a
[[ ! -f .env ]] || source .env
[[ ! -f .env.production ]] || source .env.production
set +a
export PATH="$PWD/.venv/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

case "${1:-}" in
  api)
    exec .venv/bin/uvicorn app.main:app --app-dir backend \
      --host 127.0.0.1 --port 8000 --workers 1 \
      --proxy-headers --forwarded-allow-ips 127.0.0.1
    ;;
  model)
    exec bash scripts/serve-model.sh
    ;;
  frpc)
    exec run/production/bin/frpc -c run/production/frpc.toml
    ;;
  gateway)
    export SCIDEMO_FRONTEND_ROOT="$PWD/frontend/dist"
    export XDG_CONFIG_HOME="$PWD/run/production/caddy-config"
    export XDG_DATA_HOME="$PWD/run/production/caddy-data"
    exec run/production/bin/caddy run --config deploy/local/Caddyfile --adapter caddyfile
    ;;
  *)
    echo "Usage: $0 {api|model|frpc|gateway}" >&2
    exit 2
    ;;
esac
