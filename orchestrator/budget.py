"""Transactional, integer-valued reservations; uncertain costs are never free."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from pathlib import Path

BUCKETS = {"baseline", "generation", "attack", "benign", "replay", "admission", "retry", "transfer", "ablation"}


def nonnegative(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")


class BudgetExceeded(ValueError):
    pass


class BudgetLedger:
    def __init__(self, path: Path, execution_limit: int, cost_limit_microunits: int, currency: str):
        nonnegative(execution_limit, "execution limit")
        nonnegative(cost_limit_microunits, "cost limit")
        if not currency or not isinstance(currency, str):
            raise ValueError("Currency is required, including for fixture ledgers")
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1), executions INTEGER, cost INTEGER, currency TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, bucket TEXT, executions INTEGER, reserved_cost INTEGER, actual_cost INTEGER, state TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS incidents (job_id TEXT PRIMARY KEY, code TEXT)")
            row = db.execute("SELECT executions,cost,currency FROM policy").fetchone()
            policy = (execution_limit, cost_limit_microunits, currency)
            if row is not None and tuple(row) != policy:
                raise ValueError("budget_policy_mismatch: cannot change limits on resume")
            db.execute("INSERT OR IGNORE INTO policy VALUES(1,?,?,?)", policy)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _usage(db):
        executions, cost = db.execute(
            "SELECT COALESCE(SUM(executions),0), COALESCE(SUM(CASE WHEN state='settled' THEN actual_cost ELSE reserved_cost END),0) FROM jobs WHERE state!='cancelled'").fetchone()
        return executions, cost

    def reserve(self, job_id: str, bucket: str, executions: int, max_cost_microunits: int):
        nonnegative(executions, "executions")
        nonnegative(max_cost_microunits, "maximum cost")
        if not isinstance(job_id, str) or not job_id or bucket not in BUCKETS:
            raise ValueError("Invalid job ID or budget bucket")
        with self.transaction() as db:
            prior = db.execute("SELECT bucket,executions,reserved_cost,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if prior is not None:
                if tuple(prior[:3]) != (bucket, executions, max_cost_microunits) or prior[3] == "cancelled":
                    raise ValueError("Job ID was already used with a different reservation")
                return False
            used_n, used_cost = self._usage(db)
            if db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]:
                raise BudgetExceeded("cost_overrun: ledger is blocked pending reconciliation")
            limit_n, limit_cost = db.execute("SELECT executions,cost FROM policy").fetchone()
            if used_n >= limit_n or used_cost >= limit_cost or used_n + executions > limit_n or used_cost + max_cost_microunits > limit_cost:
                raise BudgetExceeded("budget_exhausted: an execution or cost limit prevents dispatch")
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,NULL,'reserved')", (job_id, bucket, executions, max_cost_microunits))
        return True

    def start(self, job_id):
        with self.transaction() as db:
            if db.execute("UPDATE jobs SET state='running' WHERE id=? AND state='reserved'", (job_id,)).rowcount != 1:
                raise ValueError("Only a newly reserved job may start")

    def settle(self, job_id, actual_cost_microunits):
        nonnegative(actual_cost_microunits, "actual cost")
        with self.transaction() as db:
            row = db.execute("SELECT state,actual_cost,reserved_cost FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row[0] not in {"running", "settled"}:
                raise ValueError("Only a started job may settle")
            if row[0] == "settled":
                if row[1] != actual_cost_microunits:
                    raise ValueError("Conflicting duplicate settlement")
                return
            if actual_cost_microunits > row[2]:
                # Preserve actual liability and block further work instead of losing the charge.
                db.execute("UPDATE jobs SET actual_cost=?,state='settled' WHERE id=?", (actual_cost_microunits, job_id))
                db.execute("INSERT INTO incidents VALUES(?, 'cost_overrun')", (job_id,))
            else:
                db.execute("UPDATE jobs SET actual_cost=?,state='settled' WHERE id=?", (actual_cost_microunits, job_id))
        if actual_cost_microunits > row[2]:
            raise BudgetExceeded("cost_overrun: liability recorded; further dispatch blocked")

    def cancel_before_start(self, job_id):
        with self.transaction() as db:
            if db.execute("UPDATE jobs SET state='cancelled' WHERE id=? AND state='reserved'", (job_id,)).rowcount != 1:
                raise ValueError("A started or uncertain-cost job cannot be refunded")

    def summary(self):
        with self.transaction() as db:
            limits = db.execute("SELECT executions,cost,currency FROM policy").fetchone()
            usage = self._usage(db)
            buckets = db.execute("SELECT bucket,COUNT(*),SUM(executions),SUM(CASE WHEN state='settled' THEN actual_cost ELSE reserved_cost END) FROM jobs WHERE state!='cancelled' GROUP BY bucket").fetchall()
            unsettled = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('running','reserved')").fetchone()[0]
            incidents = db.execute("SELECT job_id,code FROM incidents ORDER BY job_id").fetchall()
        return dict(execution_limit=limits[0], cost_limit_microunits=limits[1], currency=limits[2],
                    executions_committed_or_reserved=usage[0], cost_committed_or_reserved=usage[1],
                    unsettled_jobs=unsettled, incidents=[dict(job_id=j, code=c) for j,c in incidents],
                    buckets=[dict(bucket=b, jobs=n, executions=e, cost_microunits=c) for b,n,e,c in buckets])
