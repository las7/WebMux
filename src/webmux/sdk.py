from __future__ import annotations

from typing import Any

import httpx

from webmux.models import ProviderName, SearchResponse, Strategy


class WebMux:
    """Small synchronous client for agent tools and application code."""

    def __init__(
        self,
        base_url: str,
        *,
        capability: str | None = None,
        user_id: str | None = None,
        org_id: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        headers: dict[str, str] = {}
        if capability:
            headers["Authorization"] = f"Bearer {capability}"
        elif user_id and org_id:
            headers.update({"X-WebMux-User-Id": user_id, "X-WebMux-Org-Id": org_id})
        else:
            raise ValueError("provide a job capability or authenticated user/org identity")
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def search(
        self,
        query: str,
        *,
        strategy: Strategy = "fallback",
        provider: ProviderName | None = None,
        providers: list[ProviderName] | None = None,
        job_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SearchResponse:
        payload: dict[str, Any] = {
            "query": query,
            "strategy": strategy,
            "options": options or {},
        }
        if provider:
            payload["provider"] = provider
        if providers:
            payload["providers"] = providers
        if job_id:
            payload["job_id"] = job_id
        response = self._client.post("/v1/search", json=payload)
        response.raise_for_status()
        return SearchResponse.model_validate(response.json())

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WebMux:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
