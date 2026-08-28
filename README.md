# WebMux

WebMux is a neutral, bring-your-own-key router for web search. V0 normalizes Brave,
Exa, and Parallel behind one API and records enough evidence to decide whether a
future automatic router is justified. It deliberately stops at search and does not
contain an ML router, billing, browser automation, or a generalized secrets system.

## Architecture

```text
agent or application
        |
        | short-lived job capability (VM path)
        v
FastAPI /v1/search
        |
        v
SearchRouter -----> rolling health (EWMA, p50, p95, errors)
        |
        v
trusted provider connector
        |
        | resolve opaque handle; attach key in memory
        +----------> Brave Web Search
        +----------> Exa Search
        +----------> Parallel Search
        |
        v
SQLite request + per-attempt telemetry
```

The production router calls one provider for `provider`, `cheapest`, and
`lowest_latency`. Only `fallback` may call another provider, and only after an
eligible failure. Fanout is isolated in the benchmark command.

The provider boundary is the small `SearchProvider` abstract class. Adding Tavily
or Serper requires an adapter, a provider configuration entry, and registration in
`Container.build`; routing and telemetry do not need provider-specific branches.

## Security model and assumptions

- Provider API keys enter only the trusted control plane over `POST /v1/credentials`.
  They are encrypted with AES-256-GCM before SQLite insertion. Authenticated
  associated data binds ciphertext to the handle, user, organization, and provider.
- API responses expose only high-entropy `cred_<provider>_...` handles. Plaintext
  keys are never returned, placed in capability tokens, or written to telemetry.
- Isolated jobs receive a signed bearer capability with a job ID, expiry, allowed
  `search.<provider>` operations, and opaque credential handles. The maximum V0 TTL
  is one hour; the default is 15 minutes.
- The trusted WebMux process resolves a handle and attaches authentication only for
  the outbound provider request. The agent cannot call this internal boundary.
- `WEBMUX_MASTER_KEY` encrypts provider keys and derives the capability signing key.
  It is a service encryption key, not a provider credential. Production should load
  it from the deployment secret store, restrict it to the WebMux service, back it up,
  and rotate it with an explicit re-encryption procedure (not included in V0).
- V0 trusts `X-WebMux-User-Id` and `X-WebMux-Org-Id`. A production ingress must
  authenticate callers, remove caller-supplied copies, and set these headers itself.
  Run WebMux behind TLS; the development server alone is not a safe internet edge.
- Capability revocation, multi-process coordination, master-key rotation, database
  retention/erasure jobs, and an external KMS are intentionally deferred.
- Queries and normalized results are retained for evaluation. They can contain
  sensitive user data; deployers must choose access controls and retention periods
  before accepting production traffic.

Long-lived provider keys must not be placed in agent environment variables, files,
command-line arguments, prompts, or model context. A provider key exists in plaintext
only in the trusted API request body during enrollment and briefly in connector
process memory while authenticating an outbound request.

## Development

Requires Python 3.11+ and `uv`.

```bash
cd ~/workspace/webmux
uv sync --all-groups
export WEBMUX_MASTER_KEY="$(uv run webmux-keygen)"
export WEBMUX_DATABASE_PATH="$PWD/webmux.sqlite3"
uv run webmux-api --host 127.0.0.1 --port 8000
```

For production, do not generate a new master key at each start. Persist it in the
deployment secret store. Losing it makes stored provider credentials unrecoverable.

Greentree is the authoritative verification entry point:

```bash
greentree test --json
```

## API workflow

All control-plane calls need authenticated identity headers in V0. Enroll provider
credentials from a trusted control-plane client; use an interactive secret prompt so
the key is not put in shell history or a command-line argument:

```python
import getpass
import httpx

headers = {
    "X-WebMux-User-Id": "user_123",
    "X-WebMux-Org-Id": "org_123",
}
with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers) as client:
    credential = client.post(
        "/v1/credentials",
        json={"provider": "exa", "api_key": getpass.getpass("Exa API key: ")},
    ).raise_for_status().json()
```

Repeat for `brave` and `parallel`. The returned object contains a handle and metadata,
never the API key.

Issue a job capability from the trusted scheduler/control plane:

```python
capability = client.post(
    "/v1/capabilities",
    json={
        "job_id": "job_123",
        "credentials": {
            "exa": "cred_exa_...",
            "brave": "cred_brave_...",
            "parallel": "cred_parallel_...",
        },
        "expires_in_seconds": 900,
    },
).raise_for_status().json()["token"]
```

Give only `capability` and the WebMux endpoint to the isolated job. The small Python
client provides the normalized interface:

```python
from webmux import WebMux

web = WebMux("http://trusted-webmux:8000", capability=capability)

result = web.search(
    "latest CUDA compatibility issues",
    strategy="fallback",
    providers=["parallel", "brave", "exa"],
    job_id="job_123",
)

exa_only = web.search(
    "papers about semantic entropy",
    provider="exa",
    job_id="job_123",
)
```

REST endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/search` | normalized search and routing |
| `POST` | `/v1/credentials` | encrypt a provider key; return a handle |
| `GET` | `/v1/credentials` | list non-secret credential metadata |
| `DELETE` | `/v1/credentials/:id` | soft-delete a credential |
| `POST` | `/v1/capabilities` | issue a short-lived, job-scoped capability |
| `GET` | `/v1/providers` | enabled providers and routing cost estimates |
| `GET` | `/v1/providers/health` | rolling health and latency metrics |
| `GET` | `/v1/requests/:id` | owner-scoped request and attempt telemetry |

Fallback is eligible after a timeout, network/provider unavailability, HTTP 5xx,
HTTP 429, invalid JSON, or an unusable result shape. Authentication and other HTTP
4xx failures stop instead of silently spending against another provider.

## Benchmark

The checked-in dataset has 200 distinct queries across eight categories. The runner
sends the exact same query separately to each provider through explicit production
routing and writes one JSONL record per query/provider pair:

```bash
uv run webmux-benchmark run \
  --dataset datasets/benchmark_v0.jsonl \
  --output benchmark-results.jsonl \
  --base-url http://127.0.0.1:8000 \
  --user-id user_123 \
  --org-id org_123 \
  --concurrency 6
```

Generate JSON and Markdown comparison reports:

```bash
uv run webmux-benchmark report \
  --results benchmark-results.jsonl \
  --output benchmark-report.json
```

Quality is intentionally not guessed from result count or provider score. Add human
or external judgments as JSONL, then rerun the report:

```json
{"query":"latest stable Linux kernel release and notable changes","provider":"brave","quality":4.5}
```

```bash
uv run webmux-benchmark report \
  --results benchmark-results.jsonl \
  --judgments benchmark-judgments.jsonl \
  --output benchmark-report.json
```

The report groups quality, cost, latency, reliability, and result count by provider
and category. It compares always-Brave, always-Exa, and always-Parallel with
retrospective category-cheapest, category-lowest-latency, category-most-reliable,
and category-quality-first policies. These are offline experiments, not an `auto`
strategy.

Before building `strategy="auto"`, collect multiple randomized runs at different
times and measure:

1. blinded human relevance, answerability, source authority, duplication, and
   freshness at fixed result counts;
2. success and usable-result rates, HTTP 429/5xx rates, timeout rates, and recovery
   time by provider;
3. warm/cold p50 and p95 provider latency by query category and geography;
4. actual provider invoice cost versus the routing estimates;
5. winner stability and the regret of each simple policy against the best provider
   per query;
6. how often the winning provider changes by category and whether the improvement
   exceeds routing complexity and fallback cost;
7. credential enrollment, capability issuance, and repeat-search conversion to test
   whether developers will keep WebMux in the request path.

If one provider wins essentially every category after repeated blinded evaluation,
that is the result: do not build an intelligent router to manufacture differentiation.

## Provider contract sources

The V0 adapters target the current official endpoints:

- [Brave Web Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get)
- [Exa Search API](https://exa.ai/docs/reference/search)
- [Parallel Search API](https://docs.parallel.ai/api-reference/search/search)

Pricing estimates are configuration based on public list prices and are not a
billing subsystem.
