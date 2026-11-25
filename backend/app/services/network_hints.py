from __future__ import annotations

import socket
from typing import Any


def get_connection_hints(*, protocol: str = "http:", remote_port: str = "3000", local_port: str = "3000") -> list[dict[str, Any]]:
    proto = protocol.rstrip(":") or "http"
    hints: list[dict[str, Any]] = [
        {
            "label": "Localhost",
            "url": f"{proto}://127.0.0.1:{local_port}",
            "kind": "local",
        }
    ]
    seen = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(info[4][0])
            if ip in seen or ip.startswith("127."):
                continue
            seen.add(ip)
            kind = "tailnet" if ip.startswith("100.") else "lan"
            hints.append(
                {
                    "label": "Tailscale" if kind == "tailnet" else "LAN",
                    "url": f"{proto}://{ip}:{remote_port}",
                    "kind": kind,
                }
            )
    except Exception:
        pass
    return hints
