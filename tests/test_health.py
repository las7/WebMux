from webmux.health import HealthSample, HealthTracker


def test_health_metrics_and_state_transitions() -> None:
    tracker = HealthTracker(["exa"], window_size=20)
    assert tracker.snapshot("exa").state == "healthy"
    for latency in (10, 20, 30, 40, 50):
        tracker.record("exa", HealthSample(True, latency, None))
    healthy = tracker.snapshot("exa")
    assert healthy.state == "healthy"
    assert healthy.p50_latency_ms == 30
    assert healthy.p95_latency_ms == 50

    for _ in range(3):
        tracker.record("exa", HealthSample(False, 100, "rate_limit"))
    unhealthy = tracker.snapshot("exa")
    assert unhealthy.state == "unhealthy"
    assert unhealthy.rate_limits == 3
    assert unhealthy.error_rate == 3 / 8
