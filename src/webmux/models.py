from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

ProviderName = Literal["brave", "exa", "parallel"]
Strategy = Literal["provider", "fallback", "cheapest", "lowest_latency"]
HealthState = Literal["healthy", "degraded", "unhealthy"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class SearchOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_results: int = Field(default=10, ge=1, le=20)
    freshness: Literal["day", "week", "month", "year"] | None = None
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    language: str | None = Field(default=None, min_length=2, max_length=10)
    provider_options: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: HttpUrl
    snippet: str
    published_at: datetime | None = None
    score: float | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    results: list[SearchItem]
    latency_ms: float = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=400)
    strategy: Strategy = "fallback"
    provider: ProviderName | None = None
    providers: list[ProviderName] | None = Field(default=None, min_length=1)
    options: SearchOptions = Field(default_factory=SearchOptions)
    job_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_routing_fields(self) -> SearchRequest:
        if self.provider is not None:
            self.strategy = "provider"
        if self.strategy == "provider" and self.provider is None:
            raise ValueError("provider is required when strategy='provider'")
        if self.provider is not None and self.providers is not None:
            raise ValueError("provider and providers cannot both be set")
        if self.providers is not None and len(set(self.providers)) != len(self.providers):
            raise ValueError("providers must not contain duplicates")
        return self


class AttemptSummary(BaseModel):
    provider: str
    attempt_number: int
    success: bool
    error_type: str | None = None
    http_status: int | None = None
    latency_ms: float
    fallback_triggered: bool


class SearchResponse(SearchResult):
    request_id: str
    strategy: Strategy
    attempts: list[AttemptSummary]


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    api_key: str = Field(min_length=1, max_length=10_000)
    name: str | None = Field(default=None, max_length=100)


class CredentialView(BaseModel):
    id: str
    provider: str
    name: str | None
    created_at: datetime
    last_used_at: datetime | None = None


class CapabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=200)
    credentials: dict[ProviderName, str] = Field(min_length=1)
    expires_in_seconds: int = Field(default=900, ge=1, le=3_600)


class CapabilityView(BaseModel):
    token: str
    token_type: Literal["Bearer"] = "Bearer"
    job_id: str
    expires_at: datetime
    allowed_operations: list[str]


class ProviderView(BaseModel):
    name: str
    enabled: bool
    estimated_cost: float


class ProviderHealthView(BaseModel):
    provider: str
    state: HealthState
    request_count: int
    success_rate: float
    error_rate: float
    rate_limits: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    ewma_latency_ms: float | None


class Principal(BaseModel):
    user_id: str
    org_id: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    provider: str | None = None
