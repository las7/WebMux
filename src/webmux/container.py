from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

import httpx

from webmux.capabilities import CapabilityBroker
from webmux.config import Settings
from webmux.credentials import CredentialVault
from webmux.health import HealthTracker
from webmux.providers.base import SearchProvider
from webmux.providers.brave import BraveProvider
from webmux.providers.exa import ExaProvider
from webmux.providers.parallel import ParallelProvider
from webmux.router import SearchRouter
from webmux.storage import Store
from webmux.telemetry import TelemetryStore


@dataclass(slots=True)
class Container:
    settings: Settings
    store: Store
    vault: CredentialVault
    capabilities: CapabilityBroker
    health: HealthTracker
    telemetry: TelemetryStore
    providers: dict[str, SearchProvider]
    router: SearchRouter
    http_client: httpx.AsyncClient

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> Container:
        store = Store(settings.database_path)
        vault = CredentialVault(store, settings.master_key)
        client = http_client or httpx.AsyncClient()
        providers: dict[str, SearchProvider] = {}
        provider_classes = {
            "parallel": ParallelProvider,
            "brave": BraveProvider,
            "exa": ExaProvider,
        }
        for name, provider_class in provider_classes.items():
            config = settings.provider_configs[name]
            providers[name] = provider_class(
                client,
                vault,
                estimated_cost=config.estimated_cost,
                timeout_seconds=config.timeout_seconds,
            )
        health = HealthTracker(
            list(providers),
            window_size=settings.health_window_size,
        )
        telemetry = TelemetryStore(store)
        signing_key = hmac.new(
            settings.master_key,
            b"webmux:v1:capability-signing",
            hashlib.sha256,
        ).digest()
        capabilities = CapabilityBroker(
            vault,
            signing_key,
            max_ttl_seconds=settings.capability_max_ttl_seconds,
        )
        router = SearchRouter(providers, settings.provider_configs, health, telemetry)
        return cls(
            settings=settings,
            store=store,
            vault=vault,
            capabilities=capabilities,
            health=health,
            telemetry=telemetry,
            providers=providers,
            router=router,
            http_client=client,
        )

    async def close(self) -> None:
        await self.http_client.aclose()
        self.store.close()
