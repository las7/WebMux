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


class ExaProvider(SearchProvider):
    name = "exa"
    endpoint = "https://api.exa.ai/search"

    async def search(
        self,
        query: str,
        options: SearchOptions,
        credential_handle: str,
    ) -> SearchResult:
        api_key = self.secrets.resolve_for_provider(credential_handle, self.name)
        payload: dict[str, Any] = {
            "query": query,
            "type": "auto",
            "numResults": options.max_results,
            "contents": {"highlights": True},
        }
        start = freshness_start(options.freshness)
        if start:
            payload["startPublishedDate"] = start.isoformat()
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
            raise UnusableProviderResponseError(self.name, "exa returned no usable results")

        results: list[SearchItem] = []
        for raw in raw_results:
            if not isinstance(raw, dict) or not raw.get("title") or not raw.get("url"):
                continue
            highlights = raw.get("highlights")
            if isinstance(highlights, list):
                snippet = clean_text(" ".join(str(item) for item in highlights))
            else:
                snippet = clean_text(raw.get("summary") or raw.get("text"))
            metadata = {
                key: value
                for key, value in raw.items()
                if key
                not in {
                    "title",
                    "url",
                    "publishedDate",
                    "score",
                    "text",
                    "highlights",
                    "summary",
                }
            }
            results.append(
                SearchItem(
                    title=clean_text(raw["title"]),
                    url=raw["url"],
                    snippet=snippet,
                    published_at=parse_datetime(raw.get("publishedDate")),
                    score=raw.get("score") if isinstance(raw.get("score"), int | float) else None,
                    provider_metadata=metadata,
                )
            )
        if not results:
            raise UnusableProviderResponseError(self.name, "exa results were malformed")
        cost = data.get("costDollars", {}).get("total", self.estimated_cost)
        if not isinstance(cost, int | float) or cost < 0:
            cost = self.estimated_cost
        return SearchResult(
            provider=self.name,
            results=results,
            latency_ms=latency_ms,
            estimated_cost=float(cost),
            metadata={
                "request_id": data.get("requestId"),
                "resolved_search_type": data.get("resolvedSearchType"),
            },
        )
