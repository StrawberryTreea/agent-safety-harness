from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.run_product_batch import (
    run_one_for_request,
    case_index_path,
    clear_case_markers,
    completed_case,
    requires_judge_preflight,
    selected_cases,
    write_case_results,
)


class ProductBatchTest(unittest.TestCase):
    def test_qoder_batch_uses_qualified_byok_entrypoint(self) -> None:
        qoder = run_one_for_request({"product": {"product_id": "qoder"}})
        codex = run_one_for_request({"product": {"product_id": "codex"}})
        self.assertEqual(qoder.name, "run_product_qoder_byok.py")
        self.assertEqual(codex.name, "run_product.py")

    def test_request_case_index_is_used_unless_explicitly_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "requests" / "product.json"
            request_path.parent.mkdir()
            configured = root / "selection.jsonl"
            override = root / "override.jsonl"
            request = {"selection": {"case_index": "../selection.jsonl"}}

            self.assertEqual(case_index_path(request_path, request), configured.resolve())
            self.assertEqual(
                case_index_path(request_path, request, override), override.resolve()
            )

    def test_selection_and_resume_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.jsonl"
            index.write_text(
                "\n".join(
                    json.dumps({"benchmark_id": "saber", "case_id": case_id})
                    for case_id in ("saber:A_1", "saber:A_2")
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(selected_cases(index, "saber", None), ["saber:A_1", "saber:A_2"])
            case_dir = root / "saber__A_1"
            case_dir.mkdir()
            (case_dir / "product_run.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            self.assertFalse(completed_case(root, "saber:A_1"))
            (case_dir / "case_result.json").write_text(
                json.dumps({"status": "failure"}), encoding="utf-8"
            )
            self.assertTrue(completed_case(root, "saber:A_1"))
            self.assertFalse(completed_case(root, "saber:A_2"))

    def test_agentdojo_resume_requires_scored_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_id = "agentdojo:travel:user_task_1:injection_task_1:important_instructions"
            case_dir = root / case_id.replace(":", "__")
            case_dir.mkdir()
            (case_dir / "product_run.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            self.assertFalse(completed_case(root, case_id))
            result = {"case_id": case_id, "status": "failure"}
            (case_dir / "case_result.json").write_text(json.dumps(result), encoding="utf-8")
            self.assertTrue(completed_case(root, case_id))
            self.assertEqual(write_case_results(root, [case_id]), 1)
            rows = (root / "case_result.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(rows[0]), result)

    def test_agentharm_resume_requires_scored_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_id = "agentharm:test_public:17-1"
            case_dir = root / case_id.replace(":", "__")
            case_dir.mkdir()
            (case_dir / "product_run.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            self.assertFalse(completed_case(root, case_id))
            result = {
                "case_id": case_id,
                "status": "invalid",
                "asr_contribution": {"numerator": 0, "denominator": 0},
            }
            (case_dir / "case_result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            self.assertTrue(completed_case(root, case_id))
            self.assertEqual(write_case_results(root, [case_id]), 1)

    def test_injecagent_resume_requires_scored_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_id = "injecagent:ds:0"
            case_root = root / case_id.replace(":", "__")
            case_root.mkdir()
            (case_root / "product_run.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            self.assertFalse(completed_case(root, case_id))
            result = {"case_id": case_id, "status": "failure"}
            (case_root / "case_result.json").write_text(json.dumps(result), encoding="utf-8")
            self.assertTrue(completed_case(root, case_id))
            self.assertEqual(write_case_results(root, [case_id]), 1)
            rows = (root / "case_result.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(rows[0]), result)

    def test_rerun_clears_stale_completion_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_id = "agentdojo:travel:user_task_1:injection_task_1:important_instructions"
            case_root = root / case_id.replace(":", "__")
            case_root.mkdir()
            for name in ("product_run.json", "case_result.json", "native_result.json"):
                (case_root / name).write_text("{}", encoding="utf-8")
            preserved = case_root / "product_stdout.jsonl"
            preserved.write_text("audit trace", encoding="utf-8")

            clear_case_markers(root, case_id)

            for name in ("product_run.json", "case_result.json", "native_result.json"):
                self.assertFalse((case_root / name).exists())
            self.assertEqual(preserved.read_text(encoding="utf-8"), "audit trace")

    def test_partial_rerun_merges_case_result_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = "msb:1:1:1:1"
            second = "msb:2:1:1:1"
            for case_id, status in ((first, "failure"), (second, "success")):
                case_root = root / case_id.replace(":", "__")
                case_root.mkdir()
                (case_root / "case_result.json").write_text(
                    json.dumps({"case_id": case_id, "status": status}), encoding="utf-8"
                )
            self.assertEqual(write_case_results(root, [first, second]), 2)
            self.assertEqual(write_case_results(root, [second]), 1)
            rows = [
                json.loads(line)
                for line in (root / "case_result.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual({row["case_id"] for row in rows}, {first, second})

    def test_multi_case_paid_run_requires_judge_preflight(self) -> None:
        self.assertFalse(
            requires_judge_preflight(case_count=1, dry_run=False, allow_unjudged_traces=False)
        )
        self.assertFalse(
            requires_judge_preflight(case_count=20, dry_run=True, allow_unjudged_traces=False)
        )
        self.assertFalse(
            requires_judge_preflight(case_count=20, dry_run=False, allow_unjudged_traces=True)
        )
        self.assertTrue(
            requires_judge_preflight(case_count=20, dry_run=False, allow_unjudged_traces=False)
        )


if __name__ == "__main__":
    unittest.main()
