from __future__ import annotations

import httpx
import pytest

from webmux.models import SearchOptions
from webmux.providers.base import MalformedProviderResponseError
from webmux.providers.brave import BraveProvider
from webmux.providers.exa import ExaProvider
from webmux.providers.parallel import ParallelProvider


class StaticResolver:
    def __init__(self, secret: str = "provider-secret") -> None:
        self.secret = secret

    def resolve_for_provider(self, credential_handle: str, provider: str) -> str:
        assert credential_handle == f"cred_{provider}_opaque"
        return self.secret


@pytest.mark.parametrize(
    ("provider_class", "host", "payload", "expected_snippet", "auth_header"),
    [
        (
            BraveProvider,
            "api.search.brave.com",
            {
                "query": {"original": "test"},
                "web": {
                    "results": [
                        {
                            "title": "<b>Brave title</b>",
                            "url": "https://example.com/brave",
                            "description": "Brave &amp; snippet",
                            "extra_snippets": ["extra"],
                        }
                    ]
                },
            },
            "Brave & snippet",
            "x-subscription-token",
        ),
        (
            ExaProvider,
            "api.exa.ai",
            {
                "requestId": "exa-request",
                "costDollars": {"total": 0.008},
                "results": [
                    {
                        "title": "Exa title",
                        "url": "https://example.com/exa",
                        "publishedDate": "2026-01-02T03:04:05Z",
                        "score": 0.92,
                        "highlights": ["First", "second"],
                        "author": "Example",
                    }
                ],
            },
            "First second",
            "x-api-key",
        ),
        (
            ParallelProvider,
            "api.parallel.ai",
            {
                "search_id": "search_123",
                "session_id": "session_123",
                "results": [
                    {
                        "title": "Parallel title",
                        "url": "https://example.com/parallel",
                        "publish_date": "2025-09-10",
                        "excerpts": ["Long excerpt", "continuation"],
                    }
                ],
            },
            "Long excerpt continuation",
            "x-api-key",
        ),
    ],
)
async def test_provider_normalization_and_authentication(
    provider_class,
    host: str,
    payload: dict,
    expected_snippet: str,
    auth_header: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == host
        assert request.headers[auth_header] == "provider-secret"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_class(
            client,
            StaticResolver(),
            estimated_cost=0.005,
            timeout_seconds=1,
        )
        result = await provider.search(
            "test",
            SearchOptions(max_results=3),
            f"cred_{provider.name}_opaque",
        )
    assert result.provider == provider.name
    assert result.results[0].snippet == expected_snippet
    assert str(result.results[0].url).startswith("https://example.com/")
    if provider.name == "exa":
        assert result.estimated_cost == 0.008


async def test_invalid_json_is_a_fallback_eligible_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BraveProvider(
            client,
            StaticResolver(),
            estimated_cost=0.005,
            timeout_seconds=1,
        )
        with pytest.raises(MalformedProviderResponseError) as raised:
            await provider.search("test", SearchOptions(), "cred_brave_opaque")
    assert raised.value.fallback_eligible is True
