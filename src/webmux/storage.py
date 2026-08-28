from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from webmux.models import utc_now


class Store:
    """Small SQLite control-plane store; one process is the intended V0 deployment."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    name TEXT,
                    ciphertext BLOB NOT NULL,
                    nonce BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS credentials_owner_provider
                    ON credentials (user_id, org_id, provider, created_at DESC);

                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    success INTEGER,
                    response_provider TEXT,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS requests_owner
                    ON requests (user_id, org_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS provider_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL REFERENCES requests(id),
                    job_id TEXT,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    success INTEGER NOT NULL,
                    error_type TEXT,
                    http_status INTEGER,
                    estimated_cost REAL NOT NULL,
                    result_count INTEGER NOT NULL,
                    fallback_triggered INTEGER NOT NULL,
                    normalized_result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS attempts_request
                    ON provider_attempts (request_id, attempt_number);
                CREATE INDEX IF NOT EXISTS attempts_provider_started
                    ON provider_attempts (provider, started_at DESC);
                """
            )
            self._connection.commit()

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            self._connection.commit()
            return cursor

    def fetchone(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchone()

    def fetchall(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, parameters).fetchall())

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def timestamp(value: datetime | None = None) -> str:
        return (value or utc_now()).isoformat()

    @staticmethod
    def json(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
