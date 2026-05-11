#!/usr/bin/env python3
import socket
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import uvicorn
import yaml


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


def _load_web_admin_config(config_file=Path("config.yaml")):
    host = DEFAULT_HOST
    port = DEFAULT_PORT

    if config_file.exists():
        try:
            config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            web_config = config.get("web_admin", {})
            host = str(web_config.get("host", host)).strip()
            port = int(web_config.get("port", port))
        except Exception as e:
            print(f"Failed to read config: {e}")

    return host or DEFAULT_HOST, port


def _host_can_bind(host):
    if host in WILDCARD_HOSTS:
        return True

    try:
        infos = socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM)
    except OSError:
        return False

    for family, socktype, proto, _, sockaddr in infos:
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.bind(sockaddr)
                return True
        except OSError:
            continue

    return False


def _resolve_bind_host(host):
    if _host_can_bind(host):
        return host

    print(
        f"Configured web_admin.host '{host}' is not available on this machine. "
        f"Binding to {DEFAULT_HOST} instead."
    )
    return DEFAULT_HOST


def main():
    configured_host, port = _load_web_admin_config()
    bind_host = _resolve_bind_host(configured_host)

    print(f"Starting Smart Checkout FastAPI Admin on http://{bind_host}:{port}")
    if bind_host == DEFAULT_HOST:
        print(f"Local URL: http://127.0.0.1:{port}")

    uvicorn.run("src.web.app:app", host=bind_host, port=port, reload=False)

if __name__ == "__main__":
    main()
