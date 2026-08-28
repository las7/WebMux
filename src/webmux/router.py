from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from pydantic import ValidationError

from webmux.access import CredentialAccess
from webmux.config import ProviderConfig
from webmux.health import HealthSample, HealthTracker
from webmux.models import AttemptSummary, SearchRequest, SearchResponse, SearchResult
from webmux.providers.base import MalformedProviderResponseError, ProviderError, SearchProvider
from webmux.telemetry import AttemptRecord, TelemetryStore


class RoutingError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.request_id = request_id
        self.provider = provider


@dataclass(frozen=True, slots=True)
class Candidate:
    provider: SearchProvider
    credential_handle: str


class SearchRouter:
    def __init__(
        self,
        providers: dict[str, SearchProvider],
        provider_configs: dict[str, ProviderConfig],
        health: HealthTracker,
        telemetry: TelemetryStore,
    ) -> None:
        self.providers = providers
        self.provider_configs = provider_configs
        self.health = health
        self.telemetry = telemetry

    async def search(self, request: SearchRequest, access: CredentialAccess) -> SearchResponse:
        request_id = f"req_{secrets.token_urlsafe(16)}"
        job_id = access.job_id
        if request.job_id is not None and request.job_id != job_id:
            raise RoutingError(
                "job_scope_mismatch",
                "request job_id does not match the authenticated job capability",
                request_id=request_id,
            )
        self.telemetry.start_request(
            request_id,
            job_id,
            access.principal,
            request.query,
            request.strategy,
        )
        try:
            candidates = self._select_candidates(request, access)
            response = await self._attempt(request_id, request, access, candidates)
        except RoutingError as exc:
            self.telemetry.finish_request(
                request_id,
                success=False,
                response_provider=None,
                error_type=exc.code,
            )
            exc.request_id = request_id
            raise
        self.telemetry.finish_request(
            request_id,
            success=True,
            response_provider=response.provider,
            error_type=None,
        )
        return response

    def _select_candidates(
        self,
        request: SearchRequest,
        access: CredentialAccess,
    ) -> list[Candidate]:
        if request.strategy == "provider":
            names = [request.provider] if request.provider else []
        else:
            names = list(request.providers or self.providers)

        eligible: list[Candidate] = []
        missing_credentials: list[str] = []
        unhealthy: list[str] = []
        for name in names:
            if name is None:
                continue
            config = self.provider_configs.get(name)
            provider = self.providers.get(name)
            if config is None or provider is None or not config.enabled:
                continue
            if not provider.compatible(request.options):
                continue
            binding = access.binding_for(name)
            if binding is None:
                missing_credentials.append(name)
                continue
            if request.strategy != "provider" and self.health.snapshot(name).state == "unhealthy":
                unhealthy.append(name)
                continue
            eligible.append(Candidate(provider=provider, credential_handle=binding.handle))

        if not eligible:
            if missing_credentials:
                raise RoutingError(
                    "credential_unavailable",
                    "no eligible provider has an authorized credential",
                )
            if unhealthy:
                raise RoutingError("providers_unhealthy", "all eligible providers are unhealthy")
            raise RoutingError("no_compatible_provider", "no provider is enabled and compatible")

        if request.strategy == "fallback" or request.strategy == "provider":
            return eligible
        if request.strategy == "cheapest":
            eligible.sort(
                key=lambda candidate: (
                    0 if self.health.snapshot(candidate.provider.name).state == "healthy" else 1,
                    self.provider_configs[candidate.provider.name].estimated_cost,
                    list(self.providers).index(candidate.provider.name),
                )
            )
            return eligible[:1]
        if request.strategy == "lowest_latency":
            eligible.sort(
                key=lambda candidate: (
                    0 if self.health.snapshot(candidate.provider.name).state == "healthy" else 1,
                    self.health.snapshot(candidate.provider.name).ewma_latency_ms
                    or self.provider_configs[candidate.provider.name].default_latency_ms,
                    list(self.providers).index(candidate.provider.name),
                )
            )
            return eligible[:1]
        raise RoutingError("invalid_strategy", f"unsupported strategy: {request.strategy}")

    async def _attempt(
        self,
        request_id: str,
        request: SearchRequest,
        access: CredentialAccess,
        candidates: list[Candidate],
    ) -> SearchResponse:
        summaries: list[AttemptSummary] = []
        last_error: ProviderError | None = None
        for index, candidate in enumerate(candidates, start=1):
            started_at = datetime.now(UTC)
            timer = perf_counter()
            result: SearchResult | None = None
            error: ProviderError | None = None
            try:
                result = await candidate.provider.search(
                    request.query,
                    request.options,
                    candidate.credential_handle,
                )
            except ProviderError as exc:
                error = exc
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                error = MalformedProviderResponseError(
                    candidate.provider.name,
                    f"{candidate.provider.name} returned a malformed response",
                )
                error.__cause__ = exc

            elapsed_ms = (perf_counter() - timer) * 1_000
            if result is not None:
                latency_ms = result.latency_ms
                estimated_cost = result.estimated_cost
                result_count = len(result.results)
                success = True
                error_type = None
                status = None
                fallback_triggered = False
            else:
                assert error is not None
                latency_ms = elapsed_ms
                estimated_cost = self.provider_configs[candidate.provider.name].estimated_cost
                result_count = 0
                success = False
                error_type = error.error_type
                status = error.http_status
                fallback_triggered = (
                    request.strategy == "fallback"
                    and error.fallback_eligible
                    and index < len(candidates)
                )

            summary = AttemptSummary(
                provider=candidate.provider.name,
                attempt_number=index,
                success=success,
                error_type=error_type,
                http_status=status,
                latency_ms=latency_ms,
                fallback_triggered=fallback_triggered,
            )
            summaries.append(summary)
            self.telemetry.record_attempt(
                AttemptRecord(
                    request_id=request_id,
                    job_id=access.job_id,
                    principal=access.principal,
                    query=request.query,
                    provider=candidate.provider.name,
                    strategy=request.strategy,
                    attempt_number=index,
                    started_at=started_at,
                    latency_ms=latency_ms,
                    success=success,
                    error_type=error_type,
                    http_status=status,
                    estimated_cost=estimated_cost,
                    result_count=result_count,
                    fallback_triggered=fallback_triggered,
                    result=result,
                )
            )
            self.health.record(
                candidate.provider.name,
                HealthSample(success=success, latency_ms=latency_ms, error_type=error_type),
            )

            if result is not None:
                return SearchResponse(
                    **result.model_dump(),
                    request_id=request_id,
                    strategy=request.strategy,
                    attempts=summaries,
                )
            last_error = error
            if request.strategy != "fallback" or not error.fallback_eligible:
                break

        assert last_error is not None
        raise RoutingError(
            last_error.error_type,
            last_error.safe_message,
            request_id=request_id,
            provider=last_error.provider,
        )
