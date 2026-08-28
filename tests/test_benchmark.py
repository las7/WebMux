from __future__ import annotations

from webmux.benchmark import build_report, load_dataset, render_markdown


def test_checked_in_dataset_has_200_diverse_queries() -> None:
    from pathlib import Path

    dataset = load_dataset(Path("datasets/benchmark_v0.jsonl"))
    assert len(dataset) == 200
    assert len({item.query for item in dataset}) == 200
    assert len({item.category for item in dataset}) == 8


def test_report_compares_always_provider_and_category_policies() -> None:
    records = []
    for category in ("fresh_current", "semantic_discovery"):
        for provider, latency, cost, success in (
            ("brave", 20, 0.005, True),
            ("exa", 30, 0.007, True),
            ("parallel", 10, 0.005, category == "fresh_current"),
        ):
            records.append(
                {
                    "query": category,
                    "category": category,
                    "provider": provider,
                    "success": success,
                    "latency_ms": latency,
                    "estimated_cost": cost,
                    "result_count": 10 if success else 0,
                }
            )
    report = build_report(records)
    assert report["policy_comparison"]["always_brave"]["reliability"] == 1
    assert report["policy_comparison"]["always_parallel"]["reliability"] == 0.5
    assert (
        report["policy_comparison"]["category_lowest_latency"]["provider_by_category"][
            "fresh_current"
        ]
        == "parallel"
    )
    assert report["by_category"]["semantic_discovery"]["exa"]["mean_quality"] is None
    assert "Quality is null" in render_markdown(report)
