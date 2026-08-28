from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from webmux.api import create_app
from webmux.config import Settings
from webmux.container import Container


@pytest.fixture
def identity_headers() -> dict[str, str]:
    return {"X-WebMux-User-Id": "user_test", "X-WebMux-Org-Id": "org_test"}


@pytest.fixture
async def build_service(tmp_path: Path):
    containers: list[Container] = []

    def build(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> tuple[Container, httpx.AsyncClient]:
        provider_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings = Settings(
            database_path=tmp_path / f"webmux-{len(containers)}.sqlite3",
            master_key=b"m" * 32,
        )
        container = Container.build(settings, http_client=provider_client)
        containers.append(container)
        api_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(container=container)),
            base_url="http://webmux.test",
        )
        return container, api_client

    yield build
    for container in containers:
        await container.close()
