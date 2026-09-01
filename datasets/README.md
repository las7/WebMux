# Benchmarking

`benchmark_v0.jsonl` contains 200 distinct queries across eight categories:

- fresh/current information
- technical documentation
- semantic discovery
- company/person discovery
- navigational queries
- simple factual lookup
- obscure long-tail research
- product/commercial research

The benchmark calls each provider through explicit routing. Production search does
not fan out.

## Run

Start WebMux and enroll Brave, Exa, and Parallel credentials for the benchmark
identity. The runner signs its identity headers, so export the same
`WEBMUX_GATEWAY_SECRET` the service uses, then run:

```bash
uv run webmux-benchmark run \
  --dataset datasets/benchmark_v0.jsonl \
  --output benchmark-results.jsonl \
  --user-id user_123 \
  --org-id org_123 \
  --concurrency 6
```

Each JSONL row retains the query/category, provider, normalized results, latency,
estimated cost, result count, error, and request ID.

## Report

```bash
uv run webmux-benchmark report \
  --results benchmark-results.jsonl \
  --output benchmark-report.json
```

This also writes a Markdown report. It compares always-Brave, always-Exa, and
always-Parallel with retrospective category policies for cost, latency,
reliability, and quality.

Quality is not inferred from result count or provider scores. Supply judgments as
JSONL when available:

```json
{"query":"latest stable Linux kernel release","provider":"brave","quality":4.5}
```

Pass them with `--judgments benchmark-judgments.jsonl`.

## Before automatic routing

Measure blinded relevance, answerability, source authority, freshness, duplication,
actual invoiced cost, p50/p95 latency, failure rates, winner stability, and routing
regret. Repeat runs at different times and geographies. If one provider consistently
wins, retain that finding instead of manufacturing an `auto` router.
