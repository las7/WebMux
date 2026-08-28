from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    enabled: bool
    estimated_cost: float
    default_latency_ms: float
    timeout_seconds: float = 10.0


DEFAULT_PROVIDERS: dict[str, ProviderConfig] = {
    # Public list prices observed 2026-08-28. These estimates are routing inputs,
    # not invoices. Keep provider-reported costs when an API returns one.
    "parallel": ProviderConfig(True, 0.005, 1_500),
    "brave": ProviderConfig(True, 0.005, 800),
    "exa": ProviderConfig(True, 0.007, 900),
}


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    master_key: bytes
    provider_configs: dict[str, ProviderConfig] = field(
        default_factory=lambda: dict(DEFAULT_PROVIDERS)
    )
    health_window_size: int = 200
    capability_max_ttl_seconds: int = 3_600

    @classmethod
    def from_env(cls) -> Settings:
        encoded_key = os.getenv("WEBMUX_MASTER_KEY")
        if not encoded_key:
            raise RuntimeError(
                "WEBMUX_MASTER_KEY is required; generate one with webmux-keygen and "
                "load it into the trusted WebMux service only"
            )
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except Exception as exc:  # pragma: no cover - exact decoder errors vary
            raise RuntimeError("WEBMUX_MASTER_KEY must be URL-safe base64") from exc
        if len(key) != 32:
            raise RuntimeError("WEBMUX_MASTER_KEY must decode to exactly 32 bytes")

        return cls(
            database_path=Path(os.getenv("WEBMUX_DATABASE_PATH", "./webmux.sqlite3")),
            master_key=key,
        )


def encode_master_key(key: bytes) -> str:
    if len(key) != 32:
        raise ValueError("master key must be 32 bytes")
    return base64.urlsafe_b64encode(key).decode("ascii")
