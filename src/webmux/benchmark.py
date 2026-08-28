from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

PROVIDERS = ("brave", "exa", "parallel")


@dataclass(frozen=True, slots=True)
class BenchmarkQuery:
    query: str
    category: str


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array")
        return data
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_dataset(path: Path) -> list[BenchmarkQuery]:
    queries: list[BenchmarkQuery] = []
    for index, item in enumerate(load_records(path), start=1):
        if not isinstance(item, dict) or not isinstance(item.get("query"), str):
            raise ValueError(f"dataset row {index} has no string query")
        if not isinstance(item.get("category"), str):
            raise ValueError(f"dataset row {index} has no string category")
        queries.append(BenchmarkQuery(query=item["query"], category=item["category"]))
    if not queries:
        raise ValueError("dataset is empty")
    return queries


async def run_one(
    client: httpx.AsyncClient,
    query: BenchmarkQuery,
    provider: str,
    *,
    max_results: int,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        response = await client.post(
            "/v1/search",
            json={
                "query": query.query,
                "strategy": "provider",
                "provider": provider,
                "options": {"max_results": max_results},
            },
        )
        elapsed_ms = (perf_counter() - started) * 1_000
        payload = response.json()
    except (httpx.RequestError, ValueError) as exc:
        return {
            "query": query.query,
            "category": query.category,
            "provider": provider,
            "success": False,
            "latency_ms": (perf_counter() - started) * 1_000,
            "estimated_cost": 0.0,
            "result_count": 0,
            "results": [],
            "error": {"code": "benchmark_client_error", "message": str(exc)},
            "request_id": None,
        }
    if response.is_success:
        return {
            "query": query.query,
            "category": query.category,
            "provider": provider,
            "success": True,
            "latency_ms": payload["latency_ms"],
            "wall_latency_ms": elapsed_ms,
            "estimated_cost": payload["estimated_cost"],
            "result_count": len(payload["results"]),
            "results": payload["results"],
            "error": None,
            "request_id": payload["request_id"],
        }
    return {
        "query": query.query,
        "category": query.category,
        "provider": provider,
        "success": False,
        "latency_ms": elapsed_ms,
        "estimated_cost": 0.0,
        "result_count": 0,
        "results": [],
        "error": payload.get("error", {"code": f"http_{response.status_code}"}),
        "request_id": payload.get("request_id"),
    }


async def run_benchmark(args: argparse.Namespace) -> None:
    dataset = load_dataset(args.dataset)
    headers = {
        "X-WebMux-User-Id": args.user_id,
        "X-WebMux-Org-Id": args.org_id,
    }
    semaphore = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=headers,
        timeout=args.timeout,
    ) as client:

        async def bounded(query: BenchmarkQuery, provider: str) -> dict[str, Any]:
            async with semaphore:
                return await run_one(client, query, provider, max_results=args.max_results)

        tasks = [
            asyncio.create_task(bounded(query, provider))
            for query in dataset
            for provider in args.providers
        ]
        with args.output.open("w", encoding="utf-8") as output:
            for task in asyncio.as_completed(tasks):
                record = await task
                output.write(json.dumps(record, separators=(",", ":")) + "\n")
                output.flush()


def percentile(values: list[float], amount: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * amount) - 1)]


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [record for record in records if record.get("success")]
    latencies = [float(record["latency_ms"]) for record in successes]
    costs = [float(record.get("estimated_cost", 0)) for record in records]
    qualities = [
        float(record["quality"])
        for record in records
        if isinstance(record.get("quality"), int | float)
    ]
    count = len(records)
    return {
        "request_count": count,
        "success_count": len(successes),
        "reliability": len(successes) / count if count else 0,
        "mean_latency_ms": statistics.fmean(latencies) if latencies else None,
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "mean_estimated_cost": statistics.fmean(costs) if costs else None,
        "total_estimated_cost": sum(costs),
        "mean_result_count": (
            statistics.fmean(float(record.get("result_count", 0)) for record in successes)
            if successes
            else 0
        ),
        "mean_quality": statistics.fmean(qualities) if qualities else None,
        "quality_judgment_count": len(qualities),
    }


def attach_judgments(
    records: list[dict[str, Any]],
    judgment_path: Path | None,
) -> None:
    if judgment_path is None:
        return
    judgments = {
        (item["query"], item["provider"]): item["quality"]
        for item in load_records(judgment_path)
    }
    for record in records:
        quality = judgments.get((record["query"], record["provider"]))
        if quality is not None:
            record["quality"] = quality


def select_category_winners(
    category_report: dict[str, dict[str, dict[str, Any]]],
    metric_name: str,
    *,
    maximize: bool,
) -> dict[str, str]:
    winners: dict[str, str] = {}
    for category, providers in category_report.items():
        eligible = [
            (provider, values[metric_name])
            for provider, values in providers.items()
            if values[metric_name] is not None
        ]
        if not eligible:
            continue
        selector = max if maximize else min
        winners[category] = selector(eligible, key=lambda item: item[1])[0]
    return winners


def evaluate_policy(
    records: list[dict[str, Any]],
    selection: dict[str, str],
) -> dict[str, Any]:
    chosen = [
        record
        for record in records
        if selection.get(record["category"]) == record["provider"]
    ]
    result = metrics(chosen)
    result["provider_by_category"] = selection
    return result


def build_report(
    records: list[dict[str, Any]],
    judgment_path: Path | None = None,
) -> dict[str, Any]:
    attach_judgments(records, judgment_path)
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        grouped[record["category"]][record["provider"]].append(record)
    category_report = {
        category: {
            provider: metrics(provider_records)
            for provider, provider_records in sorted(providers.items())
        }
        for category, providers in sorted(grouped.items())
    }

    policies: dict[str, dict[str, Any]] = {}
    categories = list(category_report)
    for provider in PROVIDERS:
        policies[f"always_{provider}"] = evaluate_policy(
            records,
            {category: provider for category in categories},
        )
    policy_definitions = {
        "category_cheapest": ("mean_estimated_cost", False),
        "category_lowest_latency": ("p50_latency_ms", False),
        "category_most_reliable": ("reliability", True),
        "category_quality_first": ("mean_quality", True),
    }
    for name, (metric_name, maximize) in policy_definitions.items():
        selection = select_category_winners(
            category_report,
            metric_name,
            maximize=maximize,
        )
        policies[name] = evaluate_policy(records, selection)

    return {
        "summary": metrics(records),
        "by_category": category_report,
        "policy_comparison": policies,
        "notes": [
            "Costs are routing estimates, not billing records.",
            "Quality is null until human or external judgments are supplied.",
            "Category policies are retrospective experiments, not production auto-routing.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# WebMux benchmark report",
        "",
        "## Policy comparison",
        "",
        "| Policy | Reliability | p50 latency (ms) | Mean cost | Mean quality |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, values in report["policy_comparison"].items():
        lines.append(
            "| {name} | {reliability:.3f} | {latency} | {cost} | {quality} |".format(
                name=name,
                reliability=values["reliability"],
                latency=(
                    f'{values["p50_latency_ms"]:.1f}'
                    if values["p50_latency_ms"] is not None
                    else "—"
                ),
                cost=(
                    f'${values["mean_estimated_cost"]:.4f}'
                    if values["mean_estimated_cost"] is not None
                    else "—"
                ),
                quality=(
                    f'{values["mean_quality"]:.2f}'
                    if values["mean_quality"] is not None
                    else "—"
                ),
            )
        )
    lines.extend(["", "## Provider metrics by query category", ""])
    for category, providers in report["by_category"].items():
        lines.extend(
            [
                f"### {category}",
                "",
                "| Provider | Reliability | p50 | p95 | Mean cost | Mean quality |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for provider, values in providers.items():
            lines.append(
                "| {provider} | {reliability:.3f} | {p50} | {p95} | {cost} | {quality} |".format(
                    provider=provider,
                    reliability=values["reliability"],
                    p50=(
                        f'{values["p50_latency_ms"]:.1f}'
                        if values["p50_latency_ms"] is not None
                        else "—"
                    ),
                    p95=(
                        f'{values["p95_latency_ms"]:.1f}'
                        if values["p95_latency_ms"] is not None
                        else "—"
                    ),
                    cost=f'${values["mean_estimated_cost"]:.4f}',
                    quality=(
                        f'{values["mean_quality"]:.2f}'
                        if values["mean_quality"] is not None
                        else "—"
                    ),
                )
            )
        lines.append("")
    lines.extend(f"- {note}" for note in report["notes"])
    return "\n".join(lines) + "\n"


def report_benchmark(args: argparse.Namespace) -> None:
    records = load_records(args.results)
    report = build_report(records, args.judgments)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path = args.markdown or args.output.with_suffix(".md")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark WebMux search providers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="fan out a dataset through explicit routing")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--base-url", default="http://127.0.0.1:8000")
    run.add_argument("--user-id", required=True)
    run.add_argument("--org-id", required=True)
    run.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    run.add_argument("--max-results", type=int, default=10)
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--timeout", type=float, default=30.0)

    report = subparsers.add_parser("report", help="compare providers and simple policies")
    report.add_argument("--results", type=Path, required=True)
    report.add_argument("--judgments", type=Path)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--markdown", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        asyncio.run(run_benchmark(args))
    else:
        report_benchmark(args)


if __name__ == "__main__":
    main()
