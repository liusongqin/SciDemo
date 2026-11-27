# 内网部署与公网转发

前端、FastAPI 和 vLLM 全部运行在内网服务器。公网服务器只运行 frps 和 Caddy，提供加密隧道及 HTTPS 转发，不部署任何前端静态文件、API 源码或模型。

```text
浏览器 https://161.114.15.77:8443
    → 公网 Caddy（所有路径转发）
    → 公网回环 127.0.0.1:18000
    → frps/frpc 加密隧道（公网 TCP 7000）
    → 内网 Caddy 127.0.0.1:8088
        ├── /      → 本机 frontend/dist（含 SPA 路由）
        └── /api/* → 本机 FastAPI 127.0.0.1:8000
                         → 本机 vLLM 127.0.0.1:8001
```

frpc 只映射内网 `8088` 到公网回环 `18000`。内网三个入口都仅监听回环地址；后端和模型不单独映射到公网。公网安全组及系统防火墙需要 TCP `7000`、`8443`；`7000` 可尽量只允许内网机器的公网出口 IP。

## 内网准备与启动

在项目根目录执行，要求现有 `.venv`、模型和前端依赖已安装：

```bash
cd /home/liusongqin/code/SciDemo
npm --prefix frontend run build
python3 scripts/setup-production.py \
  --server-addr 161.114.15.77 \
  --site-url https://161.114.15.77:8443 \
  --install-services
```

脚本下载固定版本并校验 SHA256：frp `0.72.0`、内网 Caddy `2.11.9`；生成隧道配置、证书和公网网关部署包；校验配置后安装四个 **systemd 用户服务**：

| 用户服务 | 作用 |
| --- | --- |
| `scidemo-model.service` | 本地 vLLM，回环 `8001` |
| `scidemo-api.service` | FastAPI，回环 `8000`，单 worker，无热重载 |
| `scidemo-gateway.service` | 本地 Caddy，回环 `8088`，托管本机前端并转发 API |
| `scidemo-frpc.service` | 主动连接公网 `7000`，将本地 `8088` 映射为公网回环 `18000` |

本机用户 linger 已开启，服务在退出登录后继续运行，开机无须先交互登录。脚本也会使用 `sudo -n loginctl enable-linger` 确保此设置；在其他服务器上执行时需要相应管理员权限。可通过 `loginctl show-user "$USER" -p Linger` 核查。

服务分别重启；隧道断线会重连，不需要重启模型。API 使用单 worker，因为当前任务、人工审核和 SSE 状态包含进程内数据。API 启动不代表 GPU 模型已加载完成，应检查模型接口。

## 日常服务管理（内网服务器）

在内网服务器以部署用户 `liusongqin` 执行以下命令，使用 `systemctl --user`，不加 `sudo`。日常开关服务无需重新运行安装脚本，也无需保持 SSH 会话或终端窗口。

### 开启全部服务

```bash
systemctl --user start scidemo-model scidemo-api scidemo-gateway scidemo-frpc
```

公网 frps、Caddy 也需处于运行状态；内网 frpc 会自动重连。模型需要加载时间，可以分别检查本机网关与模型是否就绪：

```bash
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8001/v1/models
```

### 关闭全部服务并释放模型显存

先结束正在运行或等待人工审核的任务，再执行：

```bash
systemctl --user stop scidemo-frpc scidemo-gateway scidemo-api scidemo-model
```

这会关闭内网隧道、网页入口、API 和本项目的模型进程。已保存的 SQLite 历史仍保留；尚在内存中的运行任务、LangGraph 人工中断状态不能跨 API 重启恢复。模型退出可能需要一段时间，确认状态与显存占用可执行：

```bash
systemctl --user status scidemo-model scidemo-api scidemo-gateway scidemo-frpc --no-pager
nvidia-smi
```

显式 `systemctl stop` 后，服务的异常重启策略不会把它立即拉起，但开机自启设置仍然保留。

### 只暂停公网访问，保留本机服务

暂停：

```bash
systemctl --user stop scidemo-frpc
```

恢复：

```bash
systemctl --user start scidemo-frpc
```

模型继续占用显存，可在恢复时直接复用；公网入口在隧道断开期间通常返回 502。公网 Caddy 继续运行，有利于公开 IP 证书自动续期。若需要释放 GPU 显存，应使用上面的“关闭全部服务”。

### 按改动重启单项服务

| 改动或目的 | 在内网执行 |
| --- | --- |
| 更新后端代码或 API 环境变量 | `systemctl --user restart scidemo-api` |
| 修改 `deploy/local/Caddyfile` | `systemctl --user restart scidemo-gateway` |
| 修改 `run/production/frpc.toml` | `systemctl --user restart scidemo-frpc` |
| 修改模型参数或需要重新加载模型 | `systemctl --user restart scidemo-model` |
| 更新前端代码 | 在项目根目录执行 `npm --prefix frontend run build`，无需重启服务 |

需要完整重启时：

```bash
systemctl --user restart scidemo-model scidemo-api scidemo-gateway scidemo-frpc
```

完整重启会重新加载 GPU 模型；日常只重启有改动的服务即可。修改 `.env.production` 后，需重启实际读取该选项的服务；例如模型参数需要重启 `scidemo-model`，仅重启 API 不会重载模型。

### 开机自启与长期停用

当前四项服务均已启用开机自启，用户 linger 已开启。`start/stop/restart` 管理当前运行状态；`enable/disable` 管理开机自启；加 `--now` 才会同时启动或停止当前进程。

立即关闭，并禁止以后开机自动启动：

```bash
systemctl --user disable --now scidemo-frpc scidemo-gateway scidemo-api scidemo-model
```

恢复开机自启，并立即开启：

```bash
systemctl --user enable --now scidemo-model scidemo-api scidemo-gateway scidemo-frpc
```

查看自启设置：

```bash
systemctl --user is-enabled scidemo-model scidemo-api scidemo-gateway scidemo-frpc
```

公网转发服务使用系统级 systemd 命令，见 [公网服务管理](public/README.md)。只停止公网转发不会停止本机模型，也不会释放 GPU 显存。

## 配置与 SSH 保留

原 `.env` 和 `scripts/start.sh` 保持原样，初次准备时另存到 `run/production/legacy-ssh/`，保留旧 SSH 方案。生产启动由 `scripts/serve-production.sh` 先加载 `.env`，再加载 `.env.production` 覆盖生产选项：

```text
FRONTEND_URL=https://161.114.15.77:8443
BUAA_CAS_SERVICE_URL=https://161.114.15.77:8443/api/auth/cas/callback
SESSION_COOKIE_SECURE=true
SSH_REVERSE_TUNNEL_ENABLED=false
VLLM_USE_FLASHINFER_SAMPLER=0
```

其中 `VLLM_USE_FLASHINFER_SAMPLER=0` 是本机针对现有 CUDA JIT 不兼容的覆盖，禁用 FlashInfer sampler，保留 `.env`；重新生成配置会保留这一自定义项。生产服务不运行旧 SSH 启动逻辑。**生产运行期间不要同时执行 `scripts/start.sh`**，以免争用模型、API 端口或重复启动旧转发。

生成的 Token、隧道 CA 和网页 CA 私钥留在内网 `run/production/`，目录权限为 `0700`。`.env.production` 和该运行目录不会提交到 Git。`run/production/legacy-ssh/.env` 包含原环境配置，也应按私密文件保管。

## 状态、日志与内网验证

以下命令应以当前部署用户执行，使用 `--user`，无需 `sudo systemctl`：

```bash
systemctl --user status scidemo-model scidemo-api scidemo-gateway scidemo-frpc --no-pager
journalctl --user -u scidemo-model -u scidemo-api -u scidemo-gateway -u scidemo-frpc -n 80 --no-pager
curl -fsS http://127.0.0.1:8088/
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8001/v1/models
```

实时跟踪日志，例如排查隧道与模型：

```bash
journalctl --user -u scidemo-frpc -f
journalctl --user -u scidemo-model -f
```

两条跟踪命令分别执行；按 `Ctrl+C` 只退出日志查看，服务继续运行。

当公网 frps 不可达时，frpc 会持续重试；这不妨碍本机验证前端、后端和模型。仅本机 health 正常不能证明公网可用；启用公开 IP 证书后可从外部验证：

```bash
curl -fsS https://161.114.15.77:8443/api/health
```

仍使用旧自建 CA 时，验证命令需额外加 `--cacert /home/liusongqin/code/SciDemo/run/production/web-ca.crt`。单服务重启及完整重启命令见上文日常服务管理。

## 公网交付与网页证书

交付文件为 `run/production/scidemo-public-server.tar.gz`，**仅含网关二进制、配置、Token 和必要的服务端证书/私钥，不包含前端、API 或模型文件**。传到公网服务器并按包内 `README.md` 执行；完整操作参见 [公网网关部署说明](public/README.md)。本次公网服务器需要安装并启动 `scidemo-frps` 和 `scidemo-caddy`，此步骤不会由内网准备脚本远程执行。

对普通公网用户开放时，可按 [公开 IP 证书切换说明](public/README.md#信任网页证书与验证) 使用 Let's Encrypt 公开 IP 证书，无需域名、无需用户导入 CA，继续使用同一 `8443` 地址。公网需要额外长期开放 TCP `80` 用于签发和自动续期；公开证书有效期为 160 小时，由 Caddy 自动续期。此前生成的原安装包保留自建 CA 方式，切换需在公网替换 Caddy 配置。

若仍使用原自建 CA 方式，访问设备需导入 `run/production/web-ca.crt`（或部署包内 `tls/web-ca.crt`）作为受信任根证书。CA 公共证书可以分发给访问者，整个部署包和任何私钥都不能分发给访问者。命令行访问时使用 `curl --cacert`，不跳过证书验证。

隧道 CA 与网页 Web CA 独立，CA 私钥仅保存在内网，不进入公网包。叶子证书有效期为 **365 天**；重新执行准备脚本时，剩余有效期不足 30 天的叶子证书会使用原 CA 重新签发，因此浏览器及 frpc 的 CA 信任无需更换。重新签发后，必须将新公网部署包传到公网并重新运行安装器，使新证书生效。手工配置的 IP 证书不会由公网 Caddy 自动续期。

## 更新与回退

更新前端只需在内网项目根目录执行：

```bash
npm --prefix frontend run build
```

内网 Caddy 直接读取 `frontend/dist/`，无需上传前端到公网，也不需要重载 GPU 模型。重新运行准备脚本可刷新网关包和配置；已在运行的模型不会因此重载。

需要回退旧 SSH 方案时，先停止并禁用四个生产用户服务，再从项目根目录执行旧启动脚本：

```bash
systemctl --user disable --now scidemo-frpc scidemo-gateway scidemo-api scidemo-model
./scripts/start.sh
```

旧脚本读取保留的原 `.env`，不读取 `.env.production`；SSH 目的主机和端口也按原 `.env` 生效，回退前应核对其仍是预期的旧入口。需要恢复生产方案时，停止旧启动脚本及其子进程，再执行上文准备与启动命令。不要同时运行两套启动方式。
