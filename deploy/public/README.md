# 公网服务器部署（frp + Caddy）

此目录是模板。实际执行使用内网脚本生成的完整网关部署包，其中只有固定版本 frps `0.72.0`、Caddy `2.11.9`、网关配置、已配对的隧道 Token 与证书，以及独立的网页 HTTPS 证书。二进制在内网准备时已校验官方 SHA256，公网无需在线安装 Caddy。公网服务器只运行 frps 和 Caddy 转发，不部署前端、后端或模型应用文件。

本次入口为 **https://161.114.15.77:8443**，不需要域名。所有请求沿同一条路径转发：

```text
浏览器 → 公网 Caddy HTTPS :8443 → 公网回环 127.0.0.1:18000
       → frps/frpc 加密隧道 → 内网 Caddy 127.0.0.1:8088
                              ├── /      → 内网前端 dist
                              └── /api/* → 内网 FastAPI 127.0.0.1:8000
                                                     → 内网 vLLM 127.0.0.1:8001
```

frpc 将内网统一入口 `8088` 映射到公网服务器的回环端口 `18000`。公网 Caddy 对所有路径执行透明反向代理，不托管静态文件、不处理 SPA 路由；这些工作全部由内网 Caddy 完成。后端和模型端口不直接映射。

## 公网服务器操作

要求 Linux、systemd、x86_64，以及 `ss`（iproute2），与包内二进制架构一致。若是 ARM 服务器，应重新生成对应架构的部署包。仅缺少 `ss` 时，Debian/Ubuntu 可执行 `sudo apt-get update` 和 `sudo apt-get install -y iproute2`。

1. 将部署包传到公网服务器并解压；包内有私钥和 Token，应保存在只有管理员可访问的目录，不能放在网站目录。
2. 云安全组和系统防火墙放行 TCP **7000** 与 **8443**。`7000` 可行时仅允许内网服务器的公网出口 IP；`8443` 用于浏览器访问。保留所需的 SSH 管理入口。此次无需开放 `80/443`。
3. **不要开放 `18000`、`8088`、`8000`、`8001`。** frps 使用 `proxyBindAddr=127.0.0.1`，应用映射仅供同机 Caddy。旧 SSH 配置仍留在内网，是否开启由其原配置控制。
4. 部署包根目录执行：

   ```bash
   sudo bash install.sh
   ```

安装器将包内二进制安装为 `/usr/local/bin/scidemo-frps`、`/usr/local/bin/scidemo-caddy`，创建 `scidemo-frps`、`scidemo-caddy` 两个独立服务和专用用户。它不修改已有 `/etc/caddy/Caddyfile`，不安装或管理发行版 Caddy 服务，不停止已有 Nginx/Caddy。若 `7000/8443` 或本机映射端口 `18000` 已被其他业务占用，会退出，需先分配其他端口并同步修改两端配置。

网关配置和证书安装到 `/etc/scidemo/`。安装器不会创建 `/opt/scidemo/` 或修改任何现有前端目录。重复安装前备份现有网关文件到 `/var/backups/scidemo/`；安装失败恢复原文件和服务状态。Caddy 关闭默认管理接口和 HTTP 自动跳转，因此此次不会占用已有 `80/443`。

## 日常开启与关闭（公网服务器）

以下命令在**公网服务器**执行，管理系统级服务，不加 `--user`：

```bash
# 开启当前服务
sudo systemctl start scidemo-frps scidemo-caddy
# 关闭当前服务
sudo systemctl stop scidemo-frps scidemo-caddy
# 重启（修改公网配置后使用）
sudo systemctl restart scidemo-frps scidemo-caddy
# 查看状态
sudo systemctl status scidemo-frps scidemo-caddy --no-pager
# 最近 80 条日志
sudo journalctl -u scidemo-frps -u scidemo-caddy -n 80 --no-pager
# 持续查看新日志，Ctrl+C 退出日志查看
sudo journalctl -u scidemo-frps -u scidemo-caddy -f
```

安装器已启用开机自启。`stop` 只停止当前运行，服务器下次重启仍会启动；`enable`/`disable` 控制开机自启，本身不改变当前运行状态。加 `--now` 才会同时开启或关闭当前服务：

```bash
sudo systemctl enable scidemo-frps scidemo-caddy
sudo systemctl disable scidemo-frps scidemo-caddy
# 同时设置开机自启并立即启动
sudo systemctl enable --now scidemo-frps scidemo-caddy
# 同时取消开机自启并立即停止
sudo systemctl disable --now scidemo-frps scidemo-caddy
```

公网只有转发服务，停止它们不会停止内网模型或释放 GPU。**仅暂停对外访问时，推荐在内网服务器执行 `systemctl --user stop scidemo-frpc`**，保留公网 Caddy 运行以自动续证；恢复时在内网执行 `systemctl --user start scidemo-frpc`。内网服务管理见 [内网部署说明](../README.md)。

使用公开 IP 证书时，有效期仅 **160 小时**；长期关闭 Caddy 会中断续期。恢复前确认公网 TCP `80` 可达，并检查 Caddy 日志确认签发或续期成功。完整链路恢复后使用 `curl -fsS https://161.114.15.77:8443/api/health` 验证，**无需 `--cacert`**。若仍使用旧自建 CA 证书，则需要客户端信任该 CA，验证方式见下节。

## 信任网页证书与验证

**面向普通公网用户时，优先使用公开受信任证书，无需用户导入 CA。** 无域名也可使用 Let's Encrypt 的公开 IP 证书；已准备 [公开 IP 证书配置](Caddyfile.public-ip)，适用于现有 Caddy `2.11.9` 和访问地址 `https://161.114.15.77:8443`。

在云安全组和系统防火墙额外开放 **TCP 80**，并保持其长期可达，以便 HTTP-01 校验及自动续期。该端口必须能由 SciDemo Caddy 监听；已有业务占用时应先安排校验流量的转发，不要停止其他业务。此配置的 `80` 只处理 ACME 校验，其他 HTTP 请求返回 404；用户仍使用 `8443` HTTPS，公网仍只转发应用请求。

从内网服务器上传新配置：

```bash
scp /home/liusongqin/code/SciDemo/deploy/public/Caddyfile.public-ip root@161.114.15.77:/root/scidemo-Caddyfile.public-ip
```

在公网服务器以 root 执行：

```bash
/usr/local/bin/scidemo-caddy validate --config /root/scidemo-Caddyfile.public-ip --adapter caddyfile
cp -a /etc/scidemo/Caddyfile /etc/scidemo/Caddyfile.private-ca.bak
install -m 0644 /root/scidemo-Caddyfile.public-ip /etc/scidemo/Caddyfile
systemctl restart scidemo-caddy
journalctl -u scidemo-caddy -n 80 --no-pager
```

证书签发成功后，直接执行 `curl -fsS https://161.114.15.77:8443/api/health`，无需 `--cacert`，浏览器和常规 API 客户端也无需导入证书。新配置不引用旧 `SCIDEMO_TLS` 手工证书变量；只清空该变量而不显式指定 ACME issuer，不能保证获得公开 IP 证书。内网配置、访问地址和 frp 隧道凭据均不用改。

公开 IP 证书必须使用 `shortlived` profile，有效期为 **160 小时**，由常驻 Caddy 自动续期。保留公网 `80` 的验证通路和 `/var/lib/scidemo-caddy/` 持久数据。若 ACME 暂时失败，查看日志和端口可达性；如需回退，可恢复 `/etc/scidemo/Caddyfile.private-ca.bak` 后重启 Caddy。重新执行原网关安装包会恢复包内的旧配置，应先将包内 `Caddyfile` 同步替换为此公开 IP 配置。

依据：[Let's Encrypt 公开 IP 证书公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/)、[ACME 校验端口说明](https://letsencrypt.org/docs/challenge-types/)。本地已验证配置可解析；公开证书是否成功签发需在公网切换后验证。

以下是保留的自建 CA 方式，仅在继续使用原手工证书时需要：

IP 的 HTTPS 证书由随包的独立 Web CA 签发，浏览器默认不会信任。将 **`tls/web-ca.crt`** 导入需要访问的设备/浏览器的“受信任的根证书颁发机构”，再打开入口。只分发 CA 公共证书；`web.key`、`server.key` 和整个部署包都不应发给普通访问者。导入 CA 后，该 CA 即可为本地受信任证书签名，故私钥必须妥善保管。

先在公网服务器验证隧道，再验证 HTTPS：

```bash
sudo systemctl status scidemo-frps scidemo-caddy --no-pager
sudo journalctl -u scidemo-frps -u scidemo-caddy -n 80 --no-pager
curl -fsS http://127.0.0.1:18000/api/health
curl --cacert tls/web-ca.crt -fsS https://161.114.15.77:8443/api/health
```

命令从解压后的包目录运行。返回 JSON 表示 API 可达。可再执行 `curl -fsS http://127.0.0.1:18000/` 验证内网前端是否可经隧道访问。所有路径都返回 502 时，检查内网 frpc、本地 Caddy `8088` 和 TCP `7000`；只有 `/api/` 返回 502 时，检查内网 FastAPI `8000`。公网 Caddy 针对 SSE 配置了 `flush_interval -1`；运行任务时应实时看到事件。

网页证书和隧道证书是两套独立证书；浏览器只需要 Web CA。frpc 已固定信任隧道 CA，不依赖浏览器信任配置。查看到期时间可执行 `openssl x509 -in tls/web.crt -noout -dates`。本模式需要到期前重新签发并更新网页证书；Caddy 不会自动续签手工配置的证书。

## 有域名后切换公开受信任的 HTTPS

将域名 A 记录指向 `161.114.15.77`（若有 AAAA，IPv6 也必须可达），允许 TCP `80/443`，确认端口未被其他服务占用。编辑 `/etc/scidemo/public.env`：

```text
SCIDEMO_SITE=https://你的域名
SCIDEMO_SERVER_NAME=你的域名
SCIDEMO_TLS=
```

`SCIDEMO_SERVER_NAME` 必须与访问地址的主机名一致，用于未发送 SNI 的 HTTPS 客户端选择默认证书。清空 `SCIDEMO_TLS` 会移除手工证书配置，Caddy 将自动申请、续期公开证书；`80` 供 HTTP-01 验证，`443` 供 HTTPS。本模板关闭 HTTP 自动跳转，需要时可在 `/etc/scidemo/Caddyfile` 移除 `auto_https disable_redirects` 后重启服务。已有网关占用端口时应将路由合入已有网关，不直接停止业务。

同时更新内网生产环境 `FRONTEND_URL`、`BUAA_CAS_SERVICE_URL` 为同一 HTTPS 域名，保持 `SESSION_COOKIE_SECURE=true`，再重启内网 API 与公网 `scidemo-caddy`。只改公网地址不更新内网配置可能使登录回调失效。域名入口验证成功后，可关闭原 `8443`。

更新前端、后端或模型只需在内网服务器执行，无需向公网服务器上传应用文件。更新隧道 Token 或证书需要同步内网 frpc，避免新旧凭据不一致。
