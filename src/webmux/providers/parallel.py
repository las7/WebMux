from __future__ import annotations

from time import perf_counter
from typing import Any

from webmux.models import SearchItem, SearchOptions, SearchResult
from webmux.providers.base import (
    SearchProvider,
    UnusableProviderResponseError,
    clean_text,
    freshness_start,
    parse_datetime,
)


class ParallelProvider(SearchProvider):
    name = "parallel"
    endpoint = "https://api.parallel.ai/v1/search"

    async def search(
        self,
        query: str,
        options: SearchOptions,
        credential_handle: str,
    ) -> SearchResult:
        api_key = self.secrets.resolve_for_provider(credential_handle, self.name)
        payload: dict[str, Any] = {
            "objective": query,
            "search_queries": [query],
            "advanced_settings": {"max_results": options.max_results},
        }
        start = freshness_start(options.freshness)
        if start:
            payload["advanced_settings"]["source_policy"] = {
                "after_date": start.date().isoformat()
            }
        payload.update(options.provider_options.get(self.name, {}))

        started = perf_counter()
        response = await self._request(
            "POST",
            self.endpoint,
            json=payload,
            headers={"Content-Type": "application/json", "x-api-key": api_key},
        )
        latency_ms = (perf_counter() - started) * 1_000
        data = self._json(response)
        raw_results = data.get("results")
        if not isinstance(raw_results, list) or not raw_results:
            raise UnusableProviderResponseError(self.name, "parallel returned no usable results")

        results: list[SearchItem] = []
        for raw in raw_results:
            if not isinstance(raw, dict) or not raw.get("title") or not raw.get("url"):
                continue
            excerpts = raw.get("excerpts")
            snippet = (
                clean_text(" ".join(str(item) for item in excerpts))
                if isinstance(excerpts, list)
                else ""
            )
            metadata = {
                key: value
                for key, value in raw.items()
                if key not in {"title", "url", "publish_date", "excerpts"}
            }
            results.append(
                SearchItem(
                    title=clean_text(raw["title"]),
                    url=raw["url"],
                    snippet=snippet,
                    published_at=parse_datetime(raw.get("publish_date")),
                    provider_metadata=metadata,
                )
            )
        if not results:
            raise UnusableProviderResponseError(self.name, "parallel results were malformed")
        return SearchResult(
            provider=self.name,
            results=results,
            latency_ms=latency_ms,
            estimated_cost=self.estimated_cost,
            metadata={
                "search_id": data.get("search_id"),
                "session_id": data.get("session_id"),
                "warnings": data.get("warnings"),
                "usage": data.get("usage"),
            },
        )
