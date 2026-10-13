from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from product_eval.adaptive import AdaptivePolicy, QueueStore, WindowMetrics, logical_shard


class AdaptiveQueueTest(unittest.TestCase):
    def make_store(self, root: Path, count: int = 64) -> QueueStore:
        store = QueueStore(root / "queue.sqlite3")
        store.add_cases(
            "codex",
            [
                {"benchmark_id": "injecagent", "case_id": f"injecagent:test:{index}"}
                for index in range(count)
            ],
        )
        return store

    def test_logical_shards_are_complete_disjoint_and_stable(self) -> None:
        assignments = {
            case_id: logical_shard("bench", case_id, 32)
            for case_id in (f"case:{index}" for index in range(1000))
        }
        self.assertEqual(len(assignments), 1000)
        self.assertTrue(all(0 <= shard < 32 for shard in assignments.values()))
        by_shard = [
            {case_id for case_id, assigned in assignments.items() if assigned == shard}
            for shard in range(32)
        ]
        self.assertEqual(set().union(*by_shard), set(assignments))
        self.assertEqual(sum(map(len, by_shard)), len(assignments))
        self.assertEqual(assignments["case:7"], logical_shard("bench", "case:7", 32))

    def test_atomic_claims_never_duplicate_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp), count=80)

            def claim(worker: int) -> str | None:
                lease = store.claim(f"worker-{worker}", "codex", lease_seconds=60)
                return lease.case_id if lease else None

            with ThreadPoolExecutor(max_workers=16) as pool:
                claimed = list(pool.map(claim, range(80)))
            self.assertNotIn(None, claimed)
            self.assertEqual(len(claimed), len(set(claimed)))
            self.assertEqual(store.counts("codex")["leased"], 80)

    def test_heartbeat_and_lease_expiry_recover_crashed_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp), count=1)
            first = store.claim("dead-worker", "codex", lease_seconds=5, now=100)
            self.assertIsNotNone(first)
            self.assertTrue(store.heartbeat(first.token, lease_seconds=5, now=103))
            self.assertEqual(store.recover_expired(now=107), 0)
            self.assertEqual(store.recover_expired(now=109), 1)
            second = store.claim("replacement", "codex", lease_seconds=5, now=110)
            self.assertEqual(second.case_id, first.case_id)
            self.assertEqual(second.attempt, 2)
            history = store.history(first.case_id)
            self.assertEqual(history[0]["outcome"], "lease_expired")

    def test_finish_is_token_owned_and_retry_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp), count=1)
            first = store.claim("worker-a", "codex", now=10)
            self.assertFalse(
                store.finish(
                    "not-the-token", outcome="failure", duration_seconds=1, now=11
                )
            )
            self.assertTrue(
                store.finish(
                    first.token,
                    outcome="invalid",
                    duration_seconds=1,
                    error_kind="missing_artifact",
                    retry=True,
                    now=11,
                )
            )
            second = store.claim("worker-b", "codex", now=12)
            self.assertEqual(second.attempt, 2)
            self.assertTrue(
                store.finish(second.token, outcome="failure", duration_seconds=2, now=14)
            )
            self.assertEqual(store.counts("codex")["complete"], 1)
            self.assertEqual(len(store.history(first.case_id)), 2)

    def test_resume_marks_only_explicitly_valid_existing_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp), count=2)
            store.mark_existing_complete(
                "codex", "injecagent", "injecagent:test:0", "failure"
            )
            claimed = store.claim("worker", "codex")
            self.assertEqual(claimed.case_id, "injecagent:test:1")

    def test_history_filters_same_case_by_product_and_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = QueueStore(Path(tmp) / "queue.sqlite3")
            row = {"benchmark_id": "injecagent", "case_id": "shared-case"}
            store.add_cases("codex", [row])
            store.add_cases("qoder", [row])
            codex = store.claim("codex-worker", "codex", now=10)
            qoder = store.claim("qoder-worker", "qoder", now=11)
            store.finish(codex.token, outcome="failure", duration_seconds=1, now=12)
            store.finish(qoder.token, outcome="failure", duration_seconds=1, now=13)
            history = store.history(
                "shared-case", product="qoder", benchmark_id="injecagent"
            )
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["product"], "qoder")

    def test_controller_events_are_durable_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp), count=1)
            first = store.record_event("controller_running", {"workers": 2})
            second = store.record_event(
                "scale_window", {"codex": {"from": 2, "to": 3}}
            )
            self.assertLess(first, second)
            self.assertEqual(
                [event["event_type"] for event in store.events()],
                ["controller_running", "scale_window"],
            )
            self.assertEqual(store.events()[1]["payload"]["codex"]["to"], 3)


class AdaptivePolicyTest(unittest.TestCase):
    def healthy(self, **overrides) -> WindowMetrics:
        values = {
            "queue_depth": 100,
            "throughput": 2.0,
            "p50_latency": 10.0,
            "p95_latency": 20.0,
            "completed": 10,
            "cpu_percent": 25.0,
            "available_memory_gb": 16.0,
            "free_disk_gb": 100.0,
            "live_workers": 2,
        }
        values.update(overrides)
        return WindowMetrics(**values)

    def test_scale_up_requires_two_healthy_windows_and_cooldown(self) -> None:
        policy = AdaptivePolicy(cooldown_seconds=300)
        self.assertEqual(policy.decide(current=2, maximum=4, metrics=self.healthy(), now=300)[0], 2)
        desired, reason = policy.decide(current=2, maximum=4, metrics=self.healthy(), now=301)
        self.assertEqual((desired, reason), (3, "healthy_scale_up"))
        self.assertEqual(policy.decide(current=3, maximum=4, metrics=self.healthy(), now=400)[0], 3)

    def test_scale_down_on_rate_limit_timeout_and_resources(self) -> None:
        policy = AdaptivePolicy()
        desired, reason = policy.decide(
            current=4,
            maximum=4,
            metrics=self.healthy(completed=6, rate_limit_errors=3),
            now=1,
        )
        self.assertEqual((desired, reason), (2, "severe_error_pressure"))
        desired, reason = policy.decide(
            current=3,
            maximum=4,
            metrics=self.healthy(available_memory_gb=2.0),
            now=2,
        )
        self.assertEqual((desired, reason), (2, "error_or_resource_pressure"))

    def test_validity_error_immediately_reduces_to_one(self) -> None:
        desired, reason = AdaptivePolicy().decide(
            current=4,
            maximum=4,
            metrics=self.healthy(model_route_errors=1),
            now=1,
        )
        self.assertEqual((desired, reason), (1, "critical_validity_error"))

    def test_forced_smoke_scale_up_and_down_are_bounded(self) -> None:
        policy = AdaptivePolicy()
        self.assertEqual(
            policy.decide(
                current=2, maximum=3, metrics=self.healthy(), now=1, force_scale="up"
            )[0],
            3,
        )
        self.assertEqual(
            policy.decide(
                current=3, maximum=3, metrics=self.healthy(), now=2, force_scale="down"
            )[0],
            2,
        )


if __name__ == "__main__":
    unittest.main()
