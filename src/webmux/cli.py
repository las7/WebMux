from __future__ import annotations

import argparse
import secrets

import uvicorn

from webmux.config import encode_master_key


def keygen_main() -> None:
    print(encode_master_key(secrets.token_bytes(32)))


def api_main() -> None:
    parser = argparse.ArgumentParser(description="Run the WebMux API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "webmux.api:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )
