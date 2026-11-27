#!/usr/bin/env bash
# Run on the PUBLIC Linux server, from an extracted deployment bundle.
set -euo pipefail
umask 077
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "${EUID}" -eq 0 ]] || { echo '请使用 sudo bash install.sh。' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo '需要运行 systemd 的 Linux 服务器。' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo '此部署包仅支持 x86_64（linux/amd64）；请为当前服务器架构重新生成部署包。' >&2; exit 1; }
for required in bin/frps bin/caddy config/frps.toml config/public.env tls/server.crt tls/server.key Caddyfile scidemo-frps.service scidemo-caddy.service; do
  [[ -s "$bundle_dir/$required" ]] || { echo "部署包缺少文件：$required" >&2; exit 1; }
done
command -v ss >/dev/null || { echo '需要 ss（iproute2）；Debian/Ubuntu 可执行 apt-get install -y iproute2。' >&2; exit 1; }
site="$(sed -n 's/^SCIDEMO_SITE=//p' "$bundle_dir/config/public.env")"
[[ "$site" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || {
  echo 'config/public.env 需要一行 SCIDEMO_SITE=https://公网IP:8443 或 https://你的域名（无引号）。' >&2
  exit 1
}
server_name="${site#*://}"
server_name="${server_name%%:*}"
configured_server_name="$(sed -n 's/^SCIDEMO_SERVER_NAME=//p' "$bundle_dir/config/public.env")"
[[ "$configured_server_name" == "$server_name" ]] || {
  echo 'SCIDEMO_SERVER_NAME 必须与 SCIDEMO_SITE 的主机名一致（无引号）。' >&2
  exit 1
}
tls_directive="$(sed -n 's/^SCIDEMO_TLS=//p' "$bundle_dir/config/public.env")"
tls_directive="${tls_directive#\"}"
tls_directive="${tls_directive%\"}"
if [[ -n "$tls_directive" ]]; then
  [[ "$tls_directive" == 'tls /etc/scidemo/tls/web.crt /etc/scidemo/tls/web.key' ]] || {
    echo 'SCIDEMO_TLS 只支持部署包内预设证书路径。' >&2; exit 1;
  }
  for required in tls/web.crt tls/web.key tls/web-ca.crt; do
    [[ -s "$bundle_dir/$required" ]] || { echo "部署包缺少文件：$required" >&2; exit 1; }
  done
fi
site_port="${site##*:}"
ports=(7000 18000)
if [[ "$site_port" =~ ^[0-9]+$ ]]; then
  (( site_port >= 1 && site_port <= 65535 )) || { echo '访问端口无效。' >&2; exit 1; }
  ports+=("$site_port")
elif [[ "$site" == https://* ]]; then
  ports+=(443)
else
  ports+=(80)
fi
# Explicit IP certificates need only the configured HTTPS port. A domain using
# ACME additionally needs 80/443 reachable for certificate validation.
[[ "$site" != https://* || -n "$tls_directive" ]] || ports+=(80 443)

frps_pid="$(systemctl show scidemo-frps.service -p MainPID --value 2>/dev/null || true)"
caddy_pid="$(systemctl show scidemo-caddy.service -p MainPID --value 2>/dev/null || true)"
for port in "${ports[@]}"; do
  while IFS= read -r listener; do
    [[ -z "$listener" ]] && continue
    if [[ "$frps_pid" =~ ^[1-9][0-9]*$ && "$listener" == *"pid=$frps_pid,"* ]] ||
       [[ "$caddy_pid" =~ ^[1-9][0-9]*$ && "$listener" == *"pid=$caddy_pid,"* ]]; then
      continue
    fi
    echo "端口 $port 已被其他服务占用；请先决定复用现有网关或调整 SciDemo 端口。" >&2
    echo "$listener" >&2
    exit 1
  done < <(ss -H -ltnp "sport = :$port")
done

for account in scidemo-frps scidemo-caddy; do
  if ! id "$account" >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$account"
  fi
done
backup_dir="/var/backups/scidemo/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup_dir"
managed_files=(
  /usr/local/bin/scidemo-frps /usr/local/bin/scidemo-caddy
  /etc/scidemo/frps.toml /etc/scidemo/public.env /etc/scidemo/Caddyfile
  /etc/scidemo/tls/server.crt /etc/scidemo/tls/server.key
  /etc/scidemo/tls/web.crt /etc/scidemo/tls/web.key /etc/scidemo/tls/web-ca.crt
  /etc/systemd/system/scidemo-frps.service /etc/systemd/system/scidemo-caddy.service
)
for target in "${managed_files[@]}"; do
  if [[ -e "$target" ]]; then
    install -d -m 0700 "$backup_dir$(dirname "$target")"
    cp -a -- "$target" "$backup_dir$target"
  fi
done
frps_was_active=false; caddy_was_active=false
frps_was_enabled=false; caddy_was_enabled=false
systemctl is-active --quiet scidemo-frps.service && frps_was_active=true
systemctl is-active --quiet scidemo-caddy.service && caddy_was_active=true
systemctl is-enabled --quiet scidemo-frps.service 2>/dev/null && frps_was_enabled=true
systemctl is-enabled --quiet scidemo-caddy.service 2>/dev/null && caddy_was_enabled=true
rollback() {
  result=$?
  trap - EXIT
  if (( result != 0 )); then
    echo "安装失败，恢复原 SciDemo 文件；备份位于 $backup_dir" >&2
    systemctl stop scidemo-caddy.service scidemo-frps.service >/dev/null 2>&1 || true
    for target in "${managed_files[@]}"; do
      if [[ -e "$backup_dir$target" ]]; then cp -a -- "$backup_dir$target" "$target"; else rm -f -- "$target"; fi
    done
    systemctl daemon-reload
    if "$frps_was_enabled"; then systemctl enable scidemo-frps.service >/dev/null 2>&1 || true; else systemctl disable scidemo-frps.service >/dev/null 2>&1 || true; fi
    if "$caddy_was_enabled"; then systemctl enable scidemo-caddy.service >/dev/null 2>&1 || true; else systemctl disable scidemo-caddy.service >/dev/null 2>&1 || true; fi
    if "$frps_was_active"; then systemctl start scidemo-frps.service || true; fi
    if "$caddy_was_active"; then systemctl start scidemo-caddy.service || true; fi
  fi
  exit "$result"
}
trap rollback EXIT

install -d -m 0755 /etc/scidemo /etc/scidemo/tls
install -m 0755 "$bundle_dir/bin/frps" /usr/local/bin/scidemo-frps
install -m 0755 "$bundle_dir/bin/caddy" /usr/local/bin/scidemo-caddy
install -m 0640 -o root -g scidemo-frps "$bundle_dir/config/frps.toml" /etc/scidemo/frps.toml
install -m 0644 "$bundle_dir/tls/server.crt" /etc/scidemo/tls/server.crt
install -m 0640 -o root -g scidemo-frps "$bundle_dir/tls/server.key" /etc/scidemo/tls/server.key
if [[ -n "$tls_directive" ]]; then
  install -m 0644 "$bundle_dir/tls/web.crt" /etc/scidemo/tls/web.crt
  install -m 0640 -o root -g scidemo-caddy "$bundle_dir/tls/web.key" /etc/scidemo/tls/web.key
  install -m 0644 "$bundle_dir/tls/web-ca.crt" /etc/scidemo/tls/web-ca.crt
fi
install -m 0640 -o root -g scidemo-caddy "$bundle_dir/config/public.env" /etc/scidemo/public.env
install -m 0644 "$bundle_dir/Caddyfile" /etc/scidemo/Caddyfile
install -m 0644 "$bundle_dir/scidemo-frps.service" /etc/systemd/system/scidemo-frps.service
install -m 0644 "$bundle_dir/scidemo-caddy.service" /etc/systemd/system/scidemo-caddy.service

/usr/local/bin/scidemo-frps verify -c /etc/scidemo/frps.toml
# systemd EnvironmentFile is not shell code. Only export the validated address.
SCIDEMO_SITE="$site" SCIDEMO_SERVER_NAME="$server_name" SCIDEMO_TLS="$tls_directive" /usr/local/bin/scidemo-caddy validate --config /etc/scidemo/Caddyfile --adapter caddyfile
systemctl daemon-reload
systemctl enable scidemo-frps.service scidemo-caddy.service
systemctl restart scidemo-frps.service
systemctl restart scidemo-caddy.service
systemctl is-active --quiet scidemo-frps.service
systemctl is-active --quiet scidemo-caddy.service
trap - EXIT
echo "SciDemo 公网服务已启动：$site"
echo "备份：$backup_dir"
echo '隧道到达后：curl -fsS http://127.0.0.1:18000/api/health'
echo '查看日志：journalctl -u scidemo-frps -u scidemo-caddy -n 80 --no-pager'
