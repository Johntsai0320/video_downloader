"""Web / Mobile server entry point for Universal Video Hub."""

from __future__ import annotations

import os
import socket

import uvicorn

from src.web.app import app


def get_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    local_ip = get_local_ip()
    print("\n" + "=" * 56)
    print("  Universal Video Hub · Web / 手機版伺服器已啟動")
    print(f"  本機瀏覽器開啟：http://127.0.0.1:{port}")
    print(f"  手機同 Wi-Fi 開啟：http://{local_ip}:{port}")
    print("=" * 56 + "\n")
    uvicorn.run(app, host=host, port=port)
