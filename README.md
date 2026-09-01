# WebMux

WebMux is a neutral, bring-your-own-key router for web search. V0 supports Brave,
Exa, and Parallel with normalized results, fallback, cost/latency routing, health,
and per-attempt telemetry.

It is intentionally search-only: no billing, browser execution, or ML routing.

## Quick start

Requires Python 3.11+ and `uv`.

```bash
uv sync --all-groups
export WEBMUX_MASTER_KEY="$(uv run webmux-keygen)"
export WEBMUX_GATEWAY_SECRET="$(uv run webmux-keygen)"
export WEBMUX_DATABASE_PATH="$PWD/webmux.sqlite3"
uv run webmux-api
```

For development only, the API starts at `http://127.0.0.1:8000`; interactive API
documentation is at `/docs`.

```python
from webmux import WebMux

web = WebMux("http://127.0.0.1:8000", capability="job-capability")

result = web.search(
    "latest CUDA compatibility issues",
    strategy="fallback",
    providers=["parallel", "brave", "exa"],
    job_id="job_123",
)

exa_only = web.search("semantic search papers", provider="exa", job_id="job_123")
```

Credentials must be enrolled and a job capability issued before using the client.
Never give provider keys to an agent workload.

Enrolling credentials and issuing capabilities is the trusted control plane's job.
It calls WebMux with an identity signed by `WEBMUX_GATEWAY_SECRET`, which WebMux
verifies before it believes any `X-WebMux-User-Id` / `X-WebMux-Org-Id` pair:

```python
control_plane = WebMux(
    "http://127.0.0.1:8000",
    user_id="user_1",
    org_id="org_1",
    gateway_secret=os.environ["WEBMUX_GATEWAY_SECRET"],
)
```

Never hand that secret to an agent workload: with it, a caller can act as any
tenant. Agents get a capability and nothing else.

## Routing

- `provider`: use one named provider.
- `fallback`: try providers in order after eligible failures.
- `cheapest`: choose the cheapest healthy eligible provider.
- `lowest_latency`: choose by recent latency.

Production search never fans out. Provider fanout exists only in the benchmark.

## Documentation

- [Architecture and API](docs/architecture.md)
- [Credential and VM security](docs/security.md)
- [Benchmark dataset and reports](datasets/README.md)
- [Provider adapters](src/webmux/providers/README.md)

## Verify

```bash
greentree test --json
```
