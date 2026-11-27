#!/usr/bin/env python3
"""Prepare this host and a matching, private public-server bundle. Never edit .env."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import secrets
import shlex
import shutil
import subprocess
import tarfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "run/production"
VERSION = "0.72.0"
SHA256 = "daf719b32ca2778770bb92955e231a07414d754378b9af328aa2642862479804"
CADDY_VERSION = "2.11.9"
CADDY_SHA256 = "5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb"
ROLES = ("model", "api", "gateway", "frpc")
WEB_ROLES = ("api", "gateway", "frpc")


def run(*args: str, quiet: bool = False) -> None:
    subprocess.run(args, check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL if quiet else None,
                   stderr=subprocess.DEVNULL if quiet else None)


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


def write_binary(path: Path, content: bytes) -> None:
    # Atomic replacement also works when the old binary is currently running.
    temporary = path.with_suffix(".new")
    temporary.write_bytes(content)
    temporary.chmod(0o755)
    temporary.replace(path)


def make_cert(directory: Path, name: str, san: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    if (directory / "server.crt").exists():
        valid = subprocess.run(["openssl", "x509", "-checkend", "2592000", "-noout", "-in",
                                str(directory / "server.crt")], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False).returncode == 0
        if valid:
            return
    # Renew the leaf certificate with the SAME CA, preserving the browsers' and frpc's trust.
    if not (directory / "ca.crt").exists():
        run("openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256",
            "-days", "3650", "-subj", f"/CN={name} CA", "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", str(directory / "ca.key"),
            "-out", str(directory / "ca.crt"), quiet=True)
    run("openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes", "-sha256",
        "-subj", f"/CN={name}", "-keyout", str(directory / "server.key"),
        "-out", str(directory / "server.csr"), quiet=True)
    private_write(directory / "extensions.cnf", f"subjectAltName={san}\n"
                  "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                  "extendedKeyUsage=serverAuth\n")
    run("openssl", "x509", "-req", "-in", str(directory / "server.csr"),
        "-CA", str(directory / "ca.crt"), "-CAkey", str(directory / "ca.key"),
        "-CAcreateserial", "-days", "365", "-sha256", "-extfile", str(directory / "extensions.cnf"),
        "-out", str(directory / "server.crt"), quiet=True)
    for path in directory.iterdir():
        path.chmod(0o600)


def install_frp() -> None:
    if platform.machine() != "x86_64":
        raise SystemExit("This pinned release is linux/amd64 only.")
    archive = RUNTIME / f"downloads/frp_{VERSION}_linux_amd64.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        run("curl", "-fLsS", "--retry", "2", "--connect-timeout", "15", "--max-time", "180",
            f"https://github.com/fatedier/frp/releases/download/v{VERSION}/{archive.name}",
            "-o", str(archive))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("frp SHA256 mismatch; remove the download and retry.")
    (RUNTIME / "bin").mkdir(exist_ok=True)
    with tarfile.open(archive) as release:
        for binary in ("frpc", "frps"):
            source = release.extractfile(f"frp_{VERSION}_linux_amd64/{binary}")
            if source is None:
                raise SystemExit(f"Missing {binary} in release")
            with source:
                write_binary(RUNTIME / "bin" / binary, source.read())


def install_caddy() -> None:
    archive = RUNTIME / f"downloads/caddy_{CADDY_VERSION}_linux_amd64.tar.gz"
    if not archive.exists():
        run("curl", "-fLsS", "--retry", "2", "--connect-timeout", "15", "--max-time", "180",
            f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/{archive.name}",
            "-o", str(archive))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != CADDY_SHA256:
        raise SystemExit("Caddy SHA256 mismatch; remove the download and retry.")
    with tarfile.open(archive) as release:
        source = release.extractfile("caddy")
        if source is None:
            raise SystemExit("Missing Caddy binary")
        with source:
            write_binary(RUNTIME / "bin/caddy", source.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-addr", required=True)
    parser.add_argument("--site-url", required=True, help="https://IP:8443 or https://domain")
    parser.add_argument("--install-services", action="store_true")
    args = parser.parse_args()
    site = urlsplit(args.site_url)
    if site.scheme != "https" or not site.hostname or site.username or site.password or site.path not in ("", "/") or site.query or site.fragment:
        parser.error("Use an HTTPS origin without path, query, credentials or fragment")
    if not args.server_addr or any(c.isspace() for c in args.server_addr) or "/" in args.server_addr:
        parser.error("Invalid --server-addr")
    site_url = args.site_url.rstrip("/")
    try:
        ipaddress.ip_address(site.hostname)
        is_ip = True
    except ValueError:
        is_ip = False
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RUNTIME.chmod(0o700)
    legacy = RUNTIME / "legacy-ssh"
    legacy.mkdir(exist_ok=True)
    for source in (ROOT / ".env", ROOT / "scripts/start.sh"):
        target = legacy / source.name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
            target.chmod(0o600)
    install_frp()
    install_caddy()
    token_file = RUNTIME / "frp.token"
    if not token_file.exists():
        private_write(token_file, secrets.token_hex(32) + "\n")
    token = token_file.read_text().strip()
    tunnel_tls = RUNTIME / "tunnel-tls"
    make_cert(tunnel_tls, "scidemo-frps", "DNS:scidemo-frps")

    client = f'''serverAddr = {json.dumps(args.server_addr)}
serverPort = 7000
loginFailExit = false
auth.method = "token"
auth.token = "{token}"
auth.additionalScopes = ["HeartBeats", "NewWorkConns"]
transport.tls.enable = true
transport.tls.serverName = "scidemo-frps"
transport.tls.trustedCaFile = {json.dumps(str(tunnel_tls / "ca.crt"))}
log.to = "console"
log.disablePrintColor = true

[[proxies]]
name = "scidemo-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8088
remotePort = 18000
'''
    private_write(RUNTIME / "frpc.toml", client)
    # .env is left unchanged, including every SSH setting. This separate file wins for production.
    env_file = ROOT / ".env.production"
    existing = env_file.read_text().splitlines() if env_file.exists() else [
        "# Production overrides; original .env and SSH configuration are preserved."]
    owned = {"FRONTEND_URL", "BUAA_CAS_SERVICE_URL", "SESSION_COOKIE_SECURE", "SSH_REVERSE_TUNNEL_ENABLED"}
    retained = [line for line in existing if line.split("=", 1)[0].strip().removeprefix("export ") not in owned]
    private_write(env_file, "\n".join(retained).rstrip() + "\n"
                  f"FRONTEND_URL={shlex.quote(site_url)}\n"
                  f"BUAA_CAS_SERVICE_URL={shlex.quote(site_url + '/api/auth/cas/callback')}\n"
                  "SESSION_COOKIE_SECURE=true\nSSH_REVERSE_TUNNEL_ENABLED=false\n")
    bundle = RUNTIME / "public-server"
    # Rebuild from an empty directory so old application assets can never remain in the relay bundle.
    if bundle.exists():
        shutil.rmtree(bundle)
    for directory in ("bin", "config", "tls"):
        (bundle / directory).mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "deploy/public").iterdir():
        if source.is_file():
            shutil.copy2(source, bundle / source.name)
    shutil.copy2(RUNTIME / "bin/frps", bundle / "bin/frps")
    shutil.copy2(RUNTIME / "bin/caddy", bundle / "bin/caddy")
    private_write(bundle / "config/frps.toml", f'''bindAddr = "0.0.0.0"
bindPort = 7000
proxyBindAddr = "127.0.0.1"
allowPorts = [{{ start = 18000, end = 18000 }}]
auth.method = "token"
auth.token = "{token}"
auth.additionalScopes = ["HeartBeats", "NewWorkConns"]
transport.tls.force = true
transport.tls.certFile = "/etc/scidemo/tls/server.crt"
transport.tls.keyFile = "/etc/scidemo/tls/server.key"
log.to = "console"
log.disablePrintColor = true
''')
    for name in ("server.crt", "server.key"):
        shutil.copy2(tunnel_tls / name, bundle / "tls" / name)
    public_env = f"SCIDEMO_SITE={site_url}\nSCIDEMO_SERVER_NAME={site.hostname}\nSCIDEMO_TLS=\n"
    if is_ip:
        web_tls = RUNTIME / f"web-tls-{site.hostname}"
        make_cert(web_tls, site.hostname, f"IP:{site.hostname}")
        for name, target in (("server.crt", "web.crt"), ("server.key", "web.key"), ("ca.crt", "web-ca.crt")):
            shutil.copy2(web_tls / name, bundle / "tls" / target)
        # A public CA certificate is safe to distribute to browsers; no CA private key leaves this host.
        shutil.copy2(web_tls / "ca.crt", RUNTIME / "web-ca.crt")
        (RUNTIME / "web-ca.crt").chmod(0o644)
        public_env = (f'SCIDEMO_SITE={site_url}\nSCIDEMO_SERVER_NAME={site.hostname}\n'
                      'SCIDEMO_TLS="tls /etc/scidemo/tls/web.crt /etc/scidemo/tls/web.key"\n')
    private_write(bundle / "config/public.env", public_env)
    dist = ROOT / "frontend/dist"
    if not (dist / "index.html").exists():
        raise SystemExit("Build the frontend first: npm --prefix frontend run build")
    private_write(RUNTIME / "deployment.json", json.dumps({"server_addr": args.server_addr,
                  "site_url": site_url, "frp_version": VERSION, "frp_sha256": SHA256,
                  "local_gateway": "127.0.0.1:8088", "public_role": "forwarding only"}, indent=2) + "\n")
    for path in (bundle / "install.sh",):
        path.chmod(0o700)
    tar_path = RUNTIME / "scidemo-public-server.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(bundle, arcname="scidemo-public-server")
    tar_path.chmod(0o600)
    # Validate before starting anything. The server cert paths belong to the destination host.
    run(str(RUNTIME / "bin/frpc"), "verify", "-c", str(RUNTIME / "frpc.toml"))
    gateway_env = dict(os.environ, SCIDEMO_FRONTEND_ROOT=str(dist),
                       XDG_CONFIG_HOME=str(RUNTIME / "caddy-config"), XDG_DATA_HOME=str(RUNTIME / "caddy-data"))
    subprocess.run([str(RUNTIME / "bin/caddy"), "validate", "--config", "deploy/local/Caddyfile",
                    "--adapter", "caddyfile"], check=True, cwd=ROOT, env=gateway_env)
    if args.install_services:
        install_services()
    print(f"Public site: {site_url}")
    print(f"Private public-server bundle: {tar_path}")
    print("Original .env and scripts/start.sh were preserved; SSH is disabled only in the production override.")


def install_services() -> None:
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    for role in ROLES:
        # Each service starts independently; the model is managed manually.
        content = (f"[Unit]\nDescription=SciDemo production {role}\nAfter=network-online.target\nStartLimitIntervalSec=0\n\n"
                   f"[Service]\nType=simple\nWorkingDirectory={str(ROOT).replace('%', '%%')}\n"
                   f"ExecStart=/bin/bash {json.dumps(str(ROOT / 'scripts/serve-production.sh'))} {role}\n"
                   "Restart=always\nRestartSec=10\nTimeoutStopSec=90\nKillMode=control-group\n"
                   "UMask=0077\nLimitNOFILE=65536\n\n[Install]\nWantedBy=default.target\n")
        private_write(units / f"scidemo-{role}.service", content)
    run("systemctl", "--user", "daemon-reload")
    run("systemd-analyze", "--user", "verify", *(str(units / f"scidemo-{role}.service")
                                               for role in ROLES))
    # Migrate existing installations without interrupting a manually started model.
    run("systemctl", "--user", "disable", "scidemo-model.service")
    run("systemctl", "--user", "enable", *(f"scidemo-{role}.service" for role in WEB_ROLES))
    # Linger is a host-level setting; enable it explicitly with existing administrator access.
    run("sudo", "-n", "loginctl", "enable-linger", str(os.getuid()))
    run("systemctl", "--user", "restart", *(f"scidemo-{role}.service" for role in WEB_ROLES))
    print("Web services started. Start the local model when needed: systemctl --user start scidemo-model")


if __name__ == "__main__":
    main()
