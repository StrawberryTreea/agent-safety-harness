from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from product_eval.selection import case_index_path, selected_cases
from tools import run_product, run_product_batch


class SelectionContractTests(unittest.TestCase):
    def test_both_entrypoints_use_the_same_functions(self):
        self.assertIs(run_product.case_index_path, run_product_batch.case_index_path)
        self.assertIs(run_product.selected_cases, run_product_batch.selected_cases)

    def test_request_index_cannot_select_a_case_only_in_global_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root/"stage.jsonl"
            selected.write_text(json.dumps({"benchmark_id": "b", "case_id": "selected"})+"\n", encoding="utf-8")
            request = {"selection": {"case_index": "stage.jsonl"}}
            path = case_index_path(root/"request.json", request)
            self.assertEqual(path, selected.resolve())
            self.assertNotIn("global-only", selected_cases(path, "b", None))
            with self.assertRaises(ValueError):
                selected_cases(path, "b", ["global-only"])

    def test_duplicate_ids_cannot_silently_change_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"index.jsonl"
            row = json.dumps({"benchmark_id": "b", "case_id": "duplicate"})
            path.write_text(row+"\n"+row+"\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                selected_cases(path, "b", None)
