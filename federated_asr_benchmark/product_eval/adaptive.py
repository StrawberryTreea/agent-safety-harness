from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable
import uuid


TERMINAL_STATES = {"complete", "failed"}


def logical_shard(benchmark_id: str, case_id: str, shard_count: int = 32) -> int:
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    digest = hashlib.sha256(f"{benchmark_id}\0{case_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


@dataclass(frozen=True)
class Lease:
    token: str
    product: str
    benchmark_id: str
    case_id: str
    logical_shard: int
    attempt: int


@dataclass(frozen=True)
class WindowMetrics:
    queue_depth: int
    throughput: float
    p50_latency: float
    p95_latency: float
    rate_limit_errors: int = 0
    server_errors: int = 0
    timeouts: int = 0
    invalid_artifacts: int = 0
    model_route_errors: int = 0
    docker_mcp_errors: int = 0
    artifact_collisions: int = 0
    completed: int = 0
    cpu_percent: float = 0.0
    available_memory_gb: float = 32.0
    free_disk_gb: float = 100.0
    live_workers: int = 0

    @property
    def observed(self) -> int:
        return max(1, self.completed + self.rate_limit_errors + self.server_errors + self.timeouts)


@dataclass
class AdaptivePolicy:
    minimum: int = 1
    healthy_windows_required: int = 2
    cooldown_seconds: float = 300.0
    healthy_windows: int = 0
    last_change_at: float = 0.0
    baseline_throughput: float | None = None

    def decide(
        self,
        *,
        current: int,
        maximum: int,
        metrics: WindowMetrics,
        now: float | None = None,
        force_scale: str | None = None,
    ) -> tuple[int, str]:
        now = time.time() if now is None else now
        current = max(self.minimum, min(current, maximum))
        if force_scale == "up" and current < maximum and metrics.queue_depth > current:
            self._changed(now)
            return current + 1, "forced_scale_up"
        if force_scale == "down" and current > self.minimum:
            self._changed(now)
            return current - 1, "forced_scale_down"

        if metrics.artifact_collisions or metrics.model_route_errors:
            self._changed(now)
            return self.minimum, "critical_validity_error"

        error_ratio = (
            metrics.rate_limit_errors
            + metrics.server_errors
            + metrics.timeouts
            + metrics.invalid_artifacts
            + metrics.docker_mcp_errors
        ) / metrics.observed
        throughput_regressed = (
            self.baseline_throughput is not None
            and metrics.completed >= 2
            and metrics.throughput < self.baseline_throughput * 0.70
        )
        pressured = (
            metrics.cpu_percent >= 95.0
            or metrics.available_memory_gb < 4.0
            or metrics.free_disk_gb < 10.0
        )
        if current > self.minimum and (error_ratio >= 0.05 or pressured or throughput_regressed):
            self._changed(now)
            if metrics.rate_limit_errors / metrics.observed >= 0.20 or metrics.timeouts / metrics.observed >= 0.20:
                return max(self.minimum, current // 2), "severe_error_pressure"
            return current - 1, "error_or_resource_pressure"

        healthy = (
            metrics.queue_depth > current
            and metrics.completed > 0
            and error_ratio == 0
            and metrics.cpu_percent < 85.0
            and metrics.available_memory_gb >= 6.0
            and metrics.free_disk_gb >= 20.0
        )
        if healthy:
            self.healthy_windows += 1
            if self.baseline_throughput is None:
                self.baseline_throughput = metrics.throughput
            else:
                self.baseline_throughput = max(self.baseline_throughput, metrics.throughput)
        else:
            self.healthy_windows = 0

        cooldown_ok = now - self.last_change_at >= self.cooldown_seconds
        if (
            healthy
            and self.healthy_windows >= self.healthy_windows_required
            and cooldown_ok
            and current < maximum
        ):
            self._changed(now)
            return current + 1, "healthy_scale_up"
        return current, "hold"

    def _changed(self, now: float) -> None:
        self.healthy_windows = 0
        self.last_change_at = now


class QueueStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    product TEXT NOT NULL,
                    benchmark_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    logical_shard INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    last_heartbeat REAL,
                    started_at REAL,
                    completed_at REAL,
                    duration_seconds REAL,
                    outcome TEXT,
                    error_kind TEXT,
                    error_detail TEXT,
                    PRIMARY KEY (product, benchmark_id, case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cases_claim
                    ON cases(product, state, logical_shard, case_id);
                CREATE TABLE IF NOT EXISTS lease_history (
                    token TEXT PRIMARY KEY,
                    product TEXT NOT NULL,
                    benchmark_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    logical_shard INTEGER NOT NULL,
                    worker_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    claimed_at REAL NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    released_at REAL,
                    outcome TEXT,
                    error_kind TEXT
                );
                CREATE TABLE IF NOT EXISTS controller_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def add_cases(
        self,
        product: str,
        rows: Iterable[dict[str, Any]],
        shard_count: int = 32,
    ) -> int:
        inserted = 0
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO cases
                    (product, benchmark_id, case_id, logical_shard)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        product,
                        row["benchmark_id"],
                        row["case_id"],
                        logical_shard(row["benchmark_id"], row["case_id"], shard_count),
                    ),
                )
                inserted += cursor.rowcount
            connection.commit()
        return inserted

    def mark_existing_complete(
        self, product: str, benchmark_id: str, case_id: str, outcome: str
    ) -> None:
        with self.session() as connection:
            connection.execute(
                """
                UPDATE cases SET state='complete', outcome=?, completed_at=?
                WHERE product=? AND benchmark_id=? AND case_id=? AND state='pending'
                """,
                (outcome, time.time(), product, benchmark_id, case_id),
            )

    def claim(
        self,
        worker_id: str,
        product: str,
        *,
        lease_seconds: float = 90.0,
        now: float | None = None,
    ) -> Lease | None:
        now = time.time() if now is None else now
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired(connection, now)
            row = connection.execute(
                """
                SELECT product, benchmark_id, case_id, logical_shard, attempt
                FROM cases
                WHERE product=? AND state='pending'
                ORDER BY logical_shard, benchmark_id, case_id
                LIMIT 1
                """,
                (product,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            token = uuid.uuid4().hex
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE cases SET state='leased', attempt=?, lease_token=?, lease_owner=?,
                    lease_expires_at=?, last_heartbeat=?, started_at=?
                WHERE product=? AND benchmark_id=? AND case_id=? AND state='pending'
                """,
                (
                    attempt,
                    token,
                    worker_id,
                    now + lease_seconds,
                    now,
                    now,
                    row["product"],
                    row["benchmark_id"],
                    row["case_id"],
                ),
            )
            connection.execute(
                """
                INSERT INTO lease_history
                (token, product, benchmark_id, case_id, logical_shard, worker_id,
                 attempt, claimed_at, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    row["product"],
                    row["benchmark_id"],
                    row["case_id"],
                    row["logical_shard"],
                    worker_id,
                    attempt,
                    now,
                    now,
                ),
            )
            connection.commit()
            return Lease(
                token=token,
                product=row["product"],
                benchmark_id=row["benchmark_id"],
                case_id=row["case_id"],
                logical_shard=row["logical_shard"],
                attempt=attempt,
            )

    def heartbeat(
        self, token: str, *, lease_seconds: float = 90.0, now: float | None = None
    ) -> bool:
        now = time.time() if now is None else now
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE cases SET lease_expires_at=?, last_heartbeat=?
                WHERE lease_token=? AND state='leased'
                """,
                (now + lease_seconds, now, token),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE lease_history SET last_heartbeat=? WHERE token=?",
                    (now, token),
                )
            connection.commit()
            return bool(cursor.rowcount)

    def finish(
        self,
        token: str,
        *,
        outcome: str,
        duration_seconds: float,
        error_kind: str | None = None,
        error_detail: str | None = None,
        retry: bool = False,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        state = "pending" if retry else ("complete" if error_kind is None else "failed")
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE cases SET state=?, completed_at=?, duration_seconds=?, outcome=?,
                    error_kind=?, error_detail=?, lease_token=NULL, lease_owner=NULL,
                    lease_expires_at=NULL
                WHERE lease_token=? AND state='leased'
                """,
                (
                    state,
                    None if retry else now,
                    duration_seconds,
                    outcome,
                    error_kind,
                    error_detail,
                    token,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE lease_history SET released_at=?, outcome=?, error_kind=?
                    WHERE token=?
                    """,
                    (now, outcome, error_kind, token),
                )
            connection.commit()
            return bool(cursor.rowcount)

    def recover_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = self._recover_expired(connection, now)
            connection.commit()
            return count

    @staticmethod
    def _recover_expired(connection: sqlite3.Connection, now: float) -> int:
        expired = connection.execute(
            "SELECT lease_token FROM cases WHERE state='leased' AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        tokens = [row["lease_token"] for row in expired]
        if not tokens:
            return 0
        connection.execute(
            """
            UPDATE cases SET state='pending', lease_token=NULL, lease_owner=NULL,
                lease_expires_at=NULL, error_kind='lease_expired',
                error_detail='worker heartbeat expired'
            WHERE state='leased' AND lease_expires_at < ?
            """,
            (now,),
        )
        connection.executemany(
            """
            UPDATE lease_history SET released_at=?, outcome='lease_expired',
                error_kind='lease_expired' WHERE token=? AND released_at IS NULL
            """,
            [(now, token) for token in tokens],
        )
        return len(tokens)

    def counts(self, product: str | None = None) -> dict[str, int]:
        where = " WHERE product=?" if product else ""
        params = (product,) if product else ()
        with self.session() as connection:
            rows = connection.execute(
                f"SELECT state, COUNT(*) AS count FROM cases{where} GROUP BY state", params
            ).fetchall()
        result = {state: 0 for state in ("pending", "leased", "complete", "failed")}
        result.update({row["state"]: row["count"] for row in rows})
        return result

    def rows(self, product: str | None = None) -> list[dict[str, Any]]:
        where = " WHERE product=?" if product else ""
        params = (product,) if product else ()
        with self.session() as connection:
            rows = connection.execute(
                f"SELECT * FROM cases{where} ORDER BY product, benchmark_id, case_id", params
            ).fetchall()
        return [dict(row) for row in rows]

    def history(
        self,
        case_id: str | None = None,
        *,
        product: str | None = None,
        benchmark_id: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[str] = []
        for column, value in (
            ("product", product),
            ("benchmark_id", benchmark_id),
            ("case_id", case_id),
        ):
            if value is not None:
                filters.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.session() as connection:
            rows = connection.execute(
                f"SELECT * FROM lease_history{where} ORDER BY claimed_at", tuple(params)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_event(self, event_type: str, payload: dict[str, Any]) -> int:
        with self.session() as connection:
            cursor = connection.execute(
                "INSERT INTO controller_events(created_at,event_type,payload_json) VALUES(?,?,?)",
                (time.time(), event_type, json.dumps(payload, sort_keys=True)),
            )
            return int(cursor.lastrowid)

    def events(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT id, created_at, event_type, payload_json "
                "FROM controller_events ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
