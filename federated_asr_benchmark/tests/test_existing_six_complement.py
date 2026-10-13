from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.build_existing_six_complement import (
    EXPECTED_COMPLEMENT,
    PACKAGE_ROOT,
    build_complement,
    write_selection,
)


class ExistingSixComplementTest(unittest.TestCase):
    def test_repository_complement_is_exact_and_reproducible(self) -> None:
        population = PACKAGE_ROOT / "case_index.jsonl"
        completed = PACKAGE_ROOT / "selections" / "v0.2" / "cumulative28" / "all.jsonl"
        first, manifest = build_complement(population, completed)
        second, _ = build_complement(population, completed)
        self.assertEqual(len(first), EXPECTED_COMPLEMENT)
        self.assertEqual(
            [row["case_id"] for row in first],
            [row["case_id"] for row in second],
        )
        self.assertTrue(manifest["proof"]["completed_and_complement_disjoint"])
        with tempfile.TemporaryDirectory() as tmp:
            write_selection(Path(tmp), first, manifest)
            self.assertEqual(
                len((Path(tmp) / "all.jsonl").read_text(encoding="utf-8").splitlines()),
                EXPECTED_COMPLEMENT,
            )


if __name__ == "__main__":
    unittest.main()
