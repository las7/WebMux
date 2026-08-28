from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from webmux.models import Principal, SearchResult, utc_now
from webmux.storage import Store


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    request_id: str
    job_id: str | None
    principal: Principal
    query: str
    provider: str
    strategy: str
    attempt_number: int
    started_at: datetime
    latency_ms: float
    success: bool
    error_type: str | None
    http_status: int | None
    estimated_cost: float
    result_count: int
    fallback_triggered: bool
    result: SearchResult | None


class TelemetryStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def start_request(
        self,
        request_id: str,
        job_id: str | None,
        principal: Principal,
        query: str,
        strategy: str,
    ) -> None:
        self.store.execute(
            """
            INSERT INTO requests (id, job_id, user_id, org_id, query, strategy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                job_id,
                principal.user_id,
                principal.org_id,
                query,
                strategy,
                utc_now().isoformat(),
            ),
        )

    def finish_request(
        self,
        request_id: str,
        *,
        success: bool,
        response_provider: str | None,
        error_type: str | None,
    ) -> None:
        self.store.execute(
            """
            UPDATE requests
            SET completed_at = ?, success = ?, response_provider = ?, error_type = ?
            WHERE id = ?
            """,
            (utc_now().isoformat(), int(success), response_provider, error_type, request_id),
        )

    def record_attempt(self, record: AttemptRecord) -> None:
        serialized_result = (
            record.result.model_dump(mode="json") if record.result is not None else None
        )
        self.store.execute(
            """
            INSERT INTO provider_attempts (
                request_id, job_id, user_id, org_id, query, provider, strategy,
                attempt_number, started_at, latency_ms, success, error_type,
                http_status, estimated_cost, result_count, fallback_triggered,
                normalized_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.request_id,
                record.job_id,
                record.principal.user_id,
                record.principal.org_id,
                record.query,
                record.provider,
                record.strategy,
                record.attempt_number,
                record.started_at.isoformat(),
                record.latency_ms,
                int(record.success),
                record.error_type,
                record.http_status,
                record.estimated_cost,
                record.result_count,
                int(record.fallback_triggered),
                Store.json(serialized_result) if serialized_result is not None else None,
            ),
        )

    def get_request(self, request_id: str, principal: Principal) -> dict[str, Any] | None:
        request_row = self.store.fetchone(
            "SELECT * FROM requests WHERE id = ? AND user_id = ? AND org_id = ?",
            (request_id, principal.user_id, principal.org_id),
        )
        if request_row is None:
            return None
        attempts = self.store.fetchall(
            "SELECT * FROM provider_attempts WHERE request_id = ? ORDER BY attempt_number",
            (request_id,),
        )
        request_data = dict(request_row)
        request_data["success"] = (
            bool(request_data["success"]) if request_data["success"] is not None else None
        )
        attempt_data: list[dict[str, Any]] = []
        for row in attempts:
            item = dict(row)
            item["success"] = bool(item["success"])
            item["fallback_triggered"] = bool(item["fallback_triggered"])
            normalized_result_json = item.pop("normalized_result_json")
            item["normalized_result"] = (
                json.loads(normalized_result_json) if normalized_result_json else None
            )
            attempt_data.append(item)
        request_data["attempts"] = attempt_data
        return request_data
