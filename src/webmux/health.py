from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass

from webmux.models import ProviderHealthView


@dataclass(frozen=True, slots=True)
class HealthSample:
    success: bool
    latency_ms: float
    error_type: str | None


class HealthTracker:
    def __init__(self, provider_names: list[str], *, window_size: int = 200) -> None:
        self._samples: dict[str, deque[HealthSample]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )
        self._ewma: dict[str, float] = {}
        for name in provider_names:
            self._samples[name]

    def record(self, provider: str, sample: HealthSample) -> None:
        self._samples[provider].append(sample)
        previous = self._ewma.get(provider)
        self._ewma[provider] = (
            sample.latency_ms if previous is None else (0.3 * sample.latency_ms + 0.7 * previous)
        )

    def snapshot(self, provider: str) -> ProviderHealthView:
        samples = list(self._samples[provider])
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
            ewma_latency_ms=self._ewma.get(provider),
        )

    def all(self) -> list[ProviderHealthView]:
        return [self.snapshot(provider) for provider in self._samples]

    @staticmethod
    def _percentile(sorted_values: list[float], percentile: float) -> float | None:
        if not sorted_values:
            return None
        index = max(0, math.ceil(percentile * len(sorted_values)) - 1)
        return sorted_values[index]
