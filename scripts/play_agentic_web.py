#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.web.app import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Play the Agentic Three Kingdoms web demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    port = _first_open_port(args.host, args.port)
    print(f"Agentic Civilization demo: http://{args.host}:{port}")
    uvicorn.run(create_app(), host=args.host, port=port, log_level="info")


def _first_open_port(host: str, start_port: int) -> int:
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"No open port found from {start_port} to {start_port + 49}")


if __name__ == "__main__":
    main()
