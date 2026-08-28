from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from webmux.models import SearchOptions, SearchResult


class SecretResolver(Protocol):
    def resolve_for_provider(self, credential_handle: str, provider: str) -> str: ...


class ProviderError(Exception):
    error_type = "provider_error"
    fallback_eligible = False

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.safe_message = message
        self.http_status = http_status


class ProviderTimeoutError(ProviderError):
    error_type = "timeout"
    fallback_eligible = True


class ProviderRateLimitError(ProviderError):
    error_type = "rate_limit"
    fallback_eligible = True


class ProviderUnavailableError(ProviderError):
    error_type = "provider_unavailable"
    fallback_eligible = True


class ProviderAuthenticationError(ProviderError):
    error_type = "authentication"


class ProviderRequestError(ProviderError):
    error_type = "invalid_request"


class MalformedProviderResponseError(ProviderError):
    error_type = "malformed_response"
    fallback_eligible = True


class UnusableProviderResponseError(ProviderError):
    error_type = "unusable_response"
    fallback_eligible = True


class SearchProvider(ABC):
    name: str

    def __init__(
        self,
        client: httpx.AsyncClient,
        secrets: SecretResolver,
        *,
        estimated_cost: float,
        timeout_seconds: float,
    ) -> None:
        self.client = client
        self.secrets = secrets
        self.estimated_cost = estimated_cost
        self.timeout_seconds = timeout_seconds

    @abstractmethod
    async def search(
        self,
        query: str,
        options: SearchOptions,
        credential_handle: str,
    ) -> SearchResult:
        """Search with an opaque handle resolved only inside this trusted connector."""

    def compatible(self, options: SearchOptions) -> bool:
        return self.name in {"brave", "exa", "parallel"} and options.max_results <= 20

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self.client.request(
                method,
                url,
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(self.name, f"{self.name} timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(self.name, f"{self.name} is unavailable") from exc

        if response.status_code == 429:
            raise ProviderRateLimitError(
                self.name,
                f"{self.name} rate limited the request",
                http_status=429,
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                self.name,
                f"{self.name} returned a server error",
                http_status=response.status_code,
            )
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                self.name,
                f"{self.name} rejected the credential",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                self.name,
                f"{self.name} rejected the request",
                http_status=response.status_code,
            )
        return response

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise MalformedProviderResponseError(
                self.name, f"{self.name} returned invalid JSON", http_status=response.status_code
            ) from exc
        if not isinstance(data, dict):
            raise MalformedProviderResponseError(
                self.name,
                f"{self.name} returned an unexpected response shape",
                http_status=response.status_code,
            )
        return data


_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value: Any, *, limit: int = 2_000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(_TAG_RE.sub(" ", value)).split())[:limit]


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def freshness_start(freshness: str | None) -> datetime | None:
    days = {"day": 1, "week": 7, "month": 31, "year": 365}
    if freshness is None:
        return None
    return datetime.now(UTC) - timedelta(days=days[freshness])
