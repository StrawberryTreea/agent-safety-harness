from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.build_v02_plan import (
    CUMULATIVE_STAGE,
    REMAINING_TWENTY_STAGE,
    TEN_PERCENT_COUNTS,
    build_plan,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class V02PlanTest(unittest.TestCase):
    def test_smoke_is_a_five_case_subset_of_ten_percent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = build_plan(
                PACKAGE_ROOT / "case_index.jsonl", root / "selections", root / "requests"
            )
            self.assertEqual(manifest["request_count"], 18)
            request = json.loads((root / "requests" / "codex_saber.json").read_text())
            self.assertEqual(
                request["output_dir"],
                "../../../.codex_tmp/federated_asr_runs/v0.2/codex/saber",
            )
            for benchmark, target in TEN_PERCENT_COUNTS.items():
                smoke = {
                    json.loads(line)["case_id"]
                    for line in (root / "selections" / "smoke5" / f"{benchmark}.jsonl").read_text().splitlines()
                }
                ten_percent = {
                    json.loads(line)["case_id"]
                    for line in (root / "selections" / "ten_percent" / f"{benchmark}.jsonl").read_text().splitlines()
                }
                remaining20 = {
                    json.loads(line)["case_id"]
                    for line in (
                        root / "selections" / REMAINING_TWENTY_STAGE / f"{benchmark}.jsonl"
                    ).read_text().splitlines()
                }
                cumulative28 = {
                    json.loads(line)["case_id"]
                    for line in (
                        root / "selections" / CUMULATIVE_STAGE / f"{benchmark}.jsonl"
                    ).read_text().splitlines()
                }
                self.assertEqual(len(smoke), 5)
                self.assertEqual(len(ten_percent), target)
                self.assertLessEqual(smoke, ten_percent)
                population = manifest["benchmarks"][benchmark]["population_count"]
                self.assertEqual(len(remaining20), (population - target + 2) // 5)
                self.assertFalse(ten_percent & remaining20)
                self.assertEqual(cumulative28, ten_percent | remaining20)


if __name__ == "__main__":
    unittest.main()
