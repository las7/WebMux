from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass

from webmux.models import HealthState, Principal, ProviderHealthView

# Health for signals that are provider-wide rather than credential-specific.
GLOBAL_SCOPE = "*"

_STATE_RANK: dict[HealthState, int] = {"healthy": 0, "degraded": 1, "unhealthy": 2}


def tenant_scope(principal: Principal) -> str:
    """Health scope of one tenant, length-prefixed so no two principals collide."""
    org_id, user_id = principal.org_id, principal.user_id
    return f"{len(org_id)}:{org_id}:{len(user_id)}:{user_id}"


@dataclass(frozen=True, slots=True)
class HealthSample:
    success: bool
    latency_ms: float
    error_type: str | None


class HealthTracker:
    """Rolling health per (scope, provider).

    A tenant's own failures steer only that tenant's routing. The shared
    `GLOBAL_SCOPE` series carries provider-wide outages, which every tenant sees.
    """

    def __init__(self, provider_names: list[str], *, window_size: int = 200) -> None:
        self._providers = list(provider_names)
        self._samples: dict[tuple[str, str], deque[HealthSample]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )
        self._ewma: dict[tuple[str, str], float] = {}
        for name in provider_names:
            self._samples[(GLOBAL_SCOPE, name)]

    def record(self, provider: str, sample: HealthSample, *, scope: str = GLOBAL_SCOPE) -> None:
        key = (scope, provider)
        self._samples[key].append(sample)
        previous = self._ewma.get(key)
        self._ewma[key] = (
            sample.latency_ms if previous is None else (0.3 * sample.latency_ms + 0.7 * previous)
        )

    def snapshot(self, provider: str, *, scope: str = GLOBAL_SCOPE) -> ProviderHealthView:
        key = (scope, provider)
        samples = list(self._samples[key])
        count = len(samples)
        successes = sum(sample.success for sample in samples)
        rate_limits = sum(sample.error_type == "rate_limit" for sample in samples)
        latencies = sorted(sample.latency_ms for sample in samples)
        success_rate = successes / count if count else 1.0
        consecutive_failures = 0
        for sample in reversed(samples):
            if sample.success:
                break
            consecutive_failures += 1

        if count >= 5 and (success_rate < 0.5 or consecutive_failures >= 3):
            state = "unhealthy"
        elif count >= 3 and (
            success_rate < 0.9 or rate_limits / count >= 0.1 or consecutive_failures >= 2
        ):
            state = "degraded"
        else:
            state = "healthy"
        return ProviderHealthView(
            provider=provider,
            state=state,
            request_count=count,
            success_rate=success_rate,
            error_rate=1 - success_rate,
            rate_limits=rate_limits,
            p50_latency_ms=self._percentile(latencies, 0.50),
            p95_latency_ms=self._percentile(latencies, 0.95),
            ewma_latency_ms=self._ewma.get(key),
        )

    def routing_state(self, provider: str, scope: str) -> HealthState:
        """Worse of what this tenant sees and what the provider shows to everyone."""
        tenant = self.snapshot(provider, scope=scope).state
        shared = self.snapshot(provider).state
        return max(tenant, shared, key=_STATE_RANK.__getitem__)

    def routing_latency_ms(self, provider: str, scope: str) -> float | None:
        latency = self._ewma.get((scope, provider))
        return latency if latency is not None else self._ewma.get((GLOBAL_SCOPE, provider))

    def report(self, scope: str) -> list[ProviderHealthView]:
        """Health as one tenant may see it: own measurements, own routing state."""
        return [
            self.snapshot(provider, scope=scope).model_copy(
                update={"state": self.routing_state(provider, scope)}
            )
            for provider in self._providers
        ]

    @staticmethod
    def _percentile(sorted_values: list[float], percentile: float) -> float | None:
        if not sorted_values:
            return None
        index = max(0, math.ceil(percentile * len(sorted_values)) - 1)
        return sorted_values[index]
