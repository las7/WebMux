from __future__ import annotations

from time import perf_counter
from typing import Any

from webmux.models import SearchItem, SearchOptions, SearchResult
from webmux.providers.base import (
    SearchProvider,
    UnusableProviderResponseError,
    clean_text,
    parse_datetime,
)


class BraveProvider(SearchProvider):
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    # Presentation and filtering knobs only. `count` stays out: it is the validated
    # result cap, and one request is one billed call whatever these say.
    allowed_provider_options = frozenset(
        {
            "extra_snippets",
            "offset",
            "result_filter",
            "safesearch",
            "spellcheck",
            "text_decorations",
            "ui_lang",
            "units",
        }
    )

    async def search(
        self,
        query: str,
        options: SearchOptions,
        credential_handle: str,
    ) -> SearchResult:
        api_key = self.secrets.resolve_for_provider(credential_handle, self.name)
        params: dict[str, Any] = {"q": query, "count": options.max_results}
        if options.freshness:
            params["freshness"] = {
                "day": "pd",
                "week": "pw",
                "month": "pm",
                "year": "py",
            }[options.freshness]
        if options.country:
            params["country"] = options.country.upper()
        if options.language:
            params["search_lang"] = options.language
        params.update(self._extra_options(options, params))

        started = perf_counter()
        response = await self._request(
            "GET",
            self.endpoint,
            params=params,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        latency_ms = (perf_counter() - started) * 1_000
        data = self._json(response)
        raw_results = data.get("web", {}).get("results")
        if not isinstance(raw_results, list) or not raw_results:
            raise UnusableProviderResponseError(self.name, "brave returned no usable web results")

        results: list[SearchItem] = []
        for raw in raw_results:
            if not isinstance(raw, dict) or not raw.get("title") or not raw.get("url"):
                continue
            metadata = {
                key: value
                for key, value in raw.items()
                if key not in {"title", "url", "description"}
            }
            results.append(
                SearchItem(
                    title=clean_text(raw["title"]),
                    url=raw["url"],
                    snippet=clean_text(raw.get("description")),
                    published_at=parse_datetime(raw.get("page_age")),
                    provider_metadata=metadata,
                )
            )
        if not results:
            raise UnusableProviderResponseError(self.name, "brave results were malformed")
        return SearchResult(
            provider=self.name,
            results=results,
            latency_ms=latency_ms,
            estimated_cost=self.estimated_cost,
            metadata={"query": data.get("query", {})},
        )
