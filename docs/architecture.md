# Architecture

```text
client / agent
      |
      v
FastAPI -> SearchRouter -> provider adapter -> search provider
                  |              |
                  v              v
               health       credential vault
                  |
                  v
              telemetry
```

## Components

- `api.py` exposes the REST surface and authenticates direct or capability-based
  requests.
- `router.py` selects providers and records every attempt.
- `providers/` contains provider-specific requests and normalization.
- `credentials.py` encrypts provider keys and resolves opaque handles.
- `capabilities.py` issues short-lived job scopes.
- `health.py` maintains rolling health and latency measurements.
- `telemetry.py` stores requests, attempts, and normalized results in SQLite.
- `benchmark.py` performs evaluation fanout outside the production router.

SQLite and in-memory health are deliberate V0 choices. The intended deployment is
one trusted WebMux process; multi-process coordination is out of scope.

## Routing behavior

| Strategy | Behavior |
| --- | --- |
| `provider` | Call the requested provider regardless of health. |
| `fallback` | Preserve the requested order, skip unhealthy providers, and continue after eligible errors. |
| `cheapest` | Select one healthy/degraded provider using configured cost. |
| `lowest_latency` | Select one healthy/degraded provider using EWMA or the configured initial latency. |

Fallback errors include timeouts, connection failures, HTTP 429/5xx, invalid JSON,
and unusable result shapes. Authentication and other HTTP 4xx errors stop routing.

## API

| Method | Path |
| --- | --- |
| `POST` | `/v1/search` |
| `POST` | `/v1/credentials` |
| `GET` | `/v1/credentials` |
| `DELETE` | `/v1/credentials/:id` |
| `POST` | `/v1/capabilities` |
| `GET` | `/v1/providers` |
| `GET` | `/v1/providers/health` |
| `GET` | `/v1/requests/:id` |

Every provider attempt records its request/job identity, query, strategy, timing,
success or error, status, estimated cost, result count, fallback decision, and
normalized results. Credentials and authorization headers are never telemetry fields.

## Scope boundary

WebMux V0 does not implement fetch, browser execution, billing, a marketplace,
provider resale, a generalized secrets platform, or automatic/ML routing.
