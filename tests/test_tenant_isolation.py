from __future__ import annotations

import httpx
import pytest
from conftest import GATEWAY_SECRET

from webmux.config import Settings, encode_master_key
from webmux.gateway import ORG_ID_HEADER, SIGNATURE_HEADER, USER_ID_HEADER, sign_identity
from webmux.sdk import WebMux

BRAVE_PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "Brave result",
                "url": "https://example.com/brave",
                "description": "brave snippet",
            }
        ]
    }
}


async def enroll(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    provider: str,
    api_key: str,
) -> str:
    response = await client.post(
        "/v1/credentials",
        headers=headers,
        json={"provider": provider, "api_key": api_key},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def brave_search(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    **options: object,
) -> httpx.Response:
    return await client.post(
        "/v1/search",
        headers=headers,
        json={"query": "isolation", "strategy": "fallback", "providers": ["brave"], **options},
    )


# 1. Identity is only an identity when the gateway signed it.


async def test_unsigned_identity_headers_are_refused(build_service) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no provider call should happen")

    _, client = build_service(handler)
    unsigned = {USER_ID_HEADER: "user_test", ORG_ID_HEADER: "org_test"}
    async with client:
        for method, path in (
            ("POST", "/v1/credentials"),
            ("GET", "/v1/credentials"),
            ("POST", "/v1/capabilities"),
            ("GET", "/v1/providers/health"),
        ):
            response = await client.request(
                method,
                path,
                headers=unsigned,
                json={"provider": "brave", "api_key": "k", "job_id": "j", "credentials": {}},
            )
            assert response.status_code == 401, path
        assert (await brave_search(client, unsigned)).status_code == 401


async def test_signature_does_not_transfer_to_another_principal(
    build_service,
    identity_headers,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=BRAVE_PAYLOAD)

    _, client = build_service(handler)
    async with client:
        await enroll(client, identity_headers, "brave", "victim-key")

        # The victim's signature replayed under the attacker's own identity.
        forged = dict(identity_headers)
        forged[USER_ID_HEADER] = "attacker"
        assert (await client.get("/v1/credentials", headers=forged)).status_code == 401

        # And the attacker's own signature does not name the victim.
        attacker = {
            USER_ID_HEADER: "user_test",
            ORG_ID_HEADER: "org_test",
            SIGNATURE_HEADER: sign_identity(GATEWAY_SECRET, "attacker", "org_attacker"),
        }
        assert (await brave_search(client, attacker)).status_code == 401
        tampered = dict(identity_headers)
        tampered[SIGNATURE_HEADER] = "not-a-signature"
        assert (await client.get("/v1/credentials", headers=tampered)).status_code == 401


async def test_missing_gateway_secret_fails_closed(build_service, identity_headers) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no provider call should happen")

    _, client = build_service(handler, gateway_secret=None)
    async with client:
        listed = await client.get("/v1/credentials", headers=identity_headers)
        searched = await brave_search(client, identity_headers)
    assert listed.status_code == 503
    assert searched.status_code == 503


def test_settings_refuse_to_start_without_a_gateway_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEBMUX_MASTER_KEY", encode_master_key(b"m" * 32))
    monkeypatch.setenv("WEBMUX_DATABASE_PATH", str(tmp_path / "webmux.sqlite3"))
    monkeypatch.delenv("WEBMUX_GATEWAY_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="WEBMUX_GATEWAY_SECRET"):
        Settings.from_env()

    monkeypatch.setenv("WEBMUX_GATEWAY_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="WEBMUX_GATEWAY_SECRET"):
        Settings.from_env()

    monkeypatch.setenv("WEBMUX_GATEWAY_SECRET", "s" * 32)
    assert Settings.from_env().gateway_secret == b"s" * 32


def test_sdk_cannot_mint_an_identity_without_the_secret() -> None:
    with pytest.raises(ValueError):
        WebMux("http://webmux.test", user_id="user_test", org_id="org_test")

    with WebMux(
        "http://webmux.test",
        user_id="user_test",
        org_id="org_test",
        gateway_secret=GATEWAY_SECRET,
    ) as client:
        assert client._client.headers[SIGNATURE_HEADER] == sign_identity(
            GATEWAY_SECRET, "user_test", "org_test"
        )


# 2. One tenant's failures stay inside that tenant.


async def test_a_bad_key_only_marks_the_provider_unhealthy_for_its_own_tenant(
    build_service,
    gateway_headers,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["x-subscription-token"] == "garbage-key":
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json=BRAVE_PAYLOAD)

    container, client = build_service(handler)
    noisy = gateway_headers("user_noisy", "org_noisy")
    quiet = gateway_headers("user_quiet", "org_quiet")
    async with client:
        await enroll(client, noisy, "brave", "garbage-key")
        await enroll(client, quiet, "brave", "working-key")

        for _ in range(5):
            failed = await brave_search(client, noisy)
            assert failed.status_code == 502
            assert failed.json()["error"]["code"] == "authentication"

        # The noisy tenant has poisoned only its own routing.
        own = await brave_search(client, noisy)
        assert own.status_code == 502
        assert own.json()["error"]["code"] == "providers_unhealthy"

        unaffected = await brave_search(client, quiet)
        assert unaffected.status_code == 200
        assert unaffected.json()["provider"] == "brave"

    assert container.health.snapshot("brave").request_count == 1
    assert container.health.snapshot("brave").state == "healthy"


async def test_provider_wide_outages_still_reach_every_tenant(
    build_service,
    gateway_headers,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    container, client = build_service(handler)
    first = gateway_headers("user_first", "org_first")
    second = gateway_headers("user_second", "org_second")
    async with client:
        await enroll(client, first, "brave", "first-key")
        await enroll(client, second, "brave", "second-key")

        for _ in range(5):
            assert (await brave_search(client, first)).status_code == 502

        other = await brave_search(client, second)
    assert container.health.snapshot("brave").state == "unhealthy"
    assert other.status_code == 502
    assert other.json()["error"]["code"] == "providers_unhealthy"


# 3. Provider endpoints require a principal and report only that tenant.


async def test_provider_endpoints_require_a_principal_and_scope_health(
    build_service,
    gateway_headers,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=BRAVE_PAYLOAD)

    _, client = build_service(handler)
    busy = gateway_headers("user_busy", "org_busy")
    idle = gateway_headers("user_idle", "org_idle")
    async with client:
        assert (await client.get("/v1/providers")).status_code == 401
        assert (await client.get("/v1/providers/health")).status_code == 401

        await enroll(client, busy, "brave", "busy-key")
        assert (await brave_search(client, busy)).status_code == 200

        assert (await client.get("/v1/providers", headers=idle)).status_code == 200
        busy_health = await client.get("/v1/providers/health", headers=busy)
        idle_health = await client.get("/v1/providers/health", headers=idle)

    busy_brave = next(item for item in busy_health.json() if item["provider"] == "brave")
    idle_brave = next(item for item in idle_health.json() if item["provider"] == "brave")
    assert busy_brave["request_count"] == 1
    assert idle_brave["request_count"] == 0
    assert idle_brave["p95_latency_ms"] is None


# 4. provider_options cannot override a validated field.


async def test_provider_options_cannot_defeat_the_result_cap(
    build_service,
    identity_headers,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=BRAVE_PAYLOAD)

    _, client = build_service(handler)
    async with client:
        await enroll(client, identity_headers, "brave", "brave-key")
        rejected = await brave_search(
            client,
            identity_headers,
            options={"max_results": 5, "provider_options": {"brave": {"count": 500}}},
        )
        assert rejected.status_code == 502
        assert rejected.json()["error"]["code"] == "invalid_request"
        assert seen == []

        allowed = await brave_search(
            client,
            identity_headers,
            options={"max_results": 5, "provider_options": {"brave": {"safesearch": "off"}}},
        )
        assert allowed.status_code == 200

    assert seen[0].url.params["count"] == "5"
    assert seen[0].url.params["safesearch"] == "off"


async def test_unlisted_provider_options_never_reach_the_provider(
    build_service,
    identity_headers,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": []})

    _, client = build_service(handler)
    async with client:
        await enroll(client, identity_headers, "exa", "exa-key")
        await enroll(client, identity_headers, "parallel", "parallel-key")

        costly_exa = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={
                "query": "expensive",
                "provider": "exa",
                "options": {"provider_options": {"exa": {"contents": {"livecrawl": "always"}}}},
            },
        )
        costly_parallel = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={
                "query": "expensive",
                "provider": "parallel",
                "options": {"provider_options": {"parallel": {"processor": "pro"}}},
            },
        )
    assert costly_exa.json()["error"]["code"] == "invalid_request"
    assert costly_parallel.json()["error"]["code"] == "invalid_request"
    assert calls == 0
