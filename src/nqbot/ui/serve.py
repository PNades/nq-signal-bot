"""`nqbot ui`: serve the panel on 127.0.0.1:<ui_port> only (SPEC §9b).

The host is fixed (no option changes it), the socket is bound here, and its bound address is read back and checked
before uvicorn starts, so the panel can never listen on another interface (nobody else on a network can open it).
"""

from __future__ import annotations

import socket
import webbrowser
from typing import Any

from nqbot.ui.app import PanelConfig, create_app

HOST = "127.0.0.1"


class BindRefused(RuntimeError):
    pass


def check_bound(sock: Any, port: int) -> None:
    """Refuse unless the socket is bound to 127.0.0.1:<port>."""
    host, bound_port = sock.getsockname()[:2]
    if host != HOST or bound_port != port:
        raise BindRefused(f"the panel's socket is bound to {host}:{bound_port}, not {HOST}:{port}; refusing to start")


def bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if hasattr(socket, "SO_EXCLUSIVEADDRUSE") else socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
    except OSError as e:
        sock.close()
        raise BindRefused(f"can't open {HOST}:{port} ({e}); is another `nqbot ui` running?") from e
    check_bound(sock, port)
    return sock


def run(cfg: PanelConfig, *, open_browser: bool = True) -> int:
    import uvicorn

    sock = bind(cfg.port)
    app = create_app(cfg)
    config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off", timeout_graceful_shutdown=1)
    server = uvicorn.Server(config)
    url = f"http://{HOST}:{cfg.port}/"
    print(f"CreamFlow on {url}  (emergency page: {url}emergency)  · Ctrl+C stops it")
    if cfg.synthetic:
        print("SYNTHETIC DATA: the panel shows the synthetic ledger and cards, labelled as such.")
    if open_browser:
        webbrowser.open(url)
    server.run(sockets=[sock])
    return 0
