from __future__ import annotations

import httpx

from webmux.health import HealthSample, tenant_scope
from webmux.models import Principal


def successful_payload(provider: str) -> dict:
    if provider == "parallel":
        return {
            "search_id": "search_1",
            "session_id": "session_1",
            "results": [
                {
                    "title": "Parallel result",
                    "url": "https://example.com/parallel",
                    "excerpts": ["parallel snippet"],
                }
            ],
        }
    if provider == "brave":
        return {
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
    return {
        "results": [
            {
                "title": "Exa result",
                "url": "https://example.com/exa",
                "highlights": ["exa snippet"],
            }
        ]
    }


async def create_credentials(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    handles = {}
    for provider in ("parallel", "brave", "exa"):
        response = await client.post(
            "/v1/credentials",
            headers=headers,
            json={"provider": provider, "api_key": f"secret-{provider}"},
        )
        assert response.status_code == 201
        handles[provider] = response.json()["id"]
    return handles


async def test_explicit_provider_routing(build_service, identity_headers) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        provider = request.url.host.split(".")[1] if "search.brave" in request.url.host else None
        if request.url.host == "api.exa.ai":
            provider = "exa"
        elif request.url.host == "api.parallel.ai":
            provider = "parallel"
        else:
            provider = "brave"
        seen.append(provider)
        return httpx.Response(200, json=successful_payload(provider))

    _, client = build_service(handler)
    async with client:
        await create_credentials(client, identity_headers)
        response = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={"query": "one provider", "provider": "exa"},
        )
    assert response.status_code == 200
    assert response.json()["provider"] == "exa"
    assert seen == ["exa"]


async def test_fallback_logs_every_attempt_and_trigger(build_service, identity_headers) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.parallel.ai":
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json=successful_payload("brave"))

    _, client = build_service(handler)
    async with client:
        await create_credentials(client, identity_headers)
        response = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={
                "query": "fallback me",
                "strategy": "fallback",
                "providers": ["parallel", "brave", "exa"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["provider"] == "brave"
        assert [attempt["provider"] for attempt in payload["attempts"]] == [
            "parallel",
            "brave",
        ]
        assert payload["attempts"][0]["error_type"] == "provider_unavailable"
        assert payload["attempts"][0]["fallback_triggered"] is True

        telemetry = await client.get(
            f"/v1/requests/{payload['request_id']}",
            headers=identity_headers,
        )
        attempts = telemetry.json()["attempts"]
        assert len(attempts) == 2
        assert attempts[0]["http_status"] == 503
        assert attempts[1]["normalized_result"]["results"][0]["title"] == "Brave result"


async def test_malformed_empty_results_trigger_fallback(build_service, identity_headers) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.parallel.ai":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json=successful_payload("brave"))

    _, client = build_service(handler)
    async with client:
        await create_credentials(client, identity_headers)
        response = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={
                "query": "unusable",
                "strategy": "fallback",
                "providers": ["parallel", "brave"],
            },
        )
    assert response.status_code == 200
    assert response.json()["attempts"][0]["error_type"] == "unusable_response"


async def test_cheapest_and_lowest_latency_selection(build_service, identity_headers) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.parallel.ai":
            provider = "parallel"
        elif request.url.host == "api.search.brave.com":
            provider = "brave"
        else:
            provider = "exa"
        seen.append(provider)
        return httpx.Response(200, json=successful_payload(provider))

    container, client = build_service(handler)
    async with client:
        await create_credentials(client, identity_headers)
        cheapest = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={"query": "cheap", "strategy": "cheapest"},
        )
        assert cheapest.json()["provider"] == "parallel"

        scope = tenant_scope(Principal(user_id="user_test", org_id="org_test"))
        for provider, latency in (("parallel", 300), ("brave", 20), ("exa", 100)):
            container.health.record(
                provider,
                HealthSample(success=True, latency_ms=latency, error_type=None),
                scope=scope,
            )
        lowest = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={"query": "fast", "strategy": "lowest_latency"},
        )
    assert lowest.status_code == 200
    assert lowest.json()["provider"] == "brave"
    assert seen == ["parallel", "brave"]


async def test_authentication_failure_does_not_fallback(build_service, identity_headers) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "bad key"})

    _, client = build_service(handler)
    async with client:
        await create_credentials(client, identity_headers)
        response = await client.post(
            "/v1/search",
            headers=identity_headers,
            json={
                "query": "bad auth",
                "strategy": "fallback",
                "providers": ["parallel", "brave"],
            },
        )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "authentication"
    assert calls == 1
