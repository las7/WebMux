from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from webmux.api import create_app
from webmux.config import Settings
from webmux.container import Container
from webmux.gateway import ORG_ID_HEADER, SIGNATURE_HEADER, USER_ID_HEADER, sign_identity

GATEWAY_SECRET = b"g" * 32


@pytest.fixture
def gateway_headers() -> Callable[[str, str], dict[str, str]]:
    def build(user_id: str, org_id: str) -> dict[str, str]:
        return {
            USER_ID_HEADER: user_id,
            ORG_ID_HEADER: org_id,
            SIGNATURE_HEADER: sign_identity(GATEWAY_SECRET, user_id, org_id),
        }

    return build


@pytest.fixture
def identity_headers(gateway_headers) -> dict[str, str]:
    return gateway_headers("user_test", "org_test")


@pytest.fixture
async def build_service(tmp_path: Path):
    containers: list[Container] = []

    def build(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        gateway_secret: bytes | None = GATEWAY_SECRET,
    ) -> tuple[Container, httpx.AsyncClient]:
        provider_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings = Settings(
            database_path=tmp_path / f"webmux-{len(containers)}.sqlite3",
            master_key=b"m" * 32,
            gateway_secret=gateway_secret,
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
