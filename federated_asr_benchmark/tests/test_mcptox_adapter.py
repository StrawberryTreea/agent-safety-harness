from __future__ import annotations

import json
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root, runtime_python

from product_eval.mcptox import MCPToxAdapter
from tools.run_product import DEFAULT_DATA_ROOT


class MCPToxAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = MCPToxAdapter(DEFAULT_DATA_ROOT / "MCPTox")

    @requires_assets(DEFAULT_DATA_ROOT / "MCPTox")
    def test_preflight_keeps_source_only_candidate_fail_closed(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(result["case_count"], 485)
        self.assertEqual(result["server_count"], 45)
        self.assertEqual(result["runnable_fragment_count"], 0)
        self.assertFalse(result["formal_release_ready"])
        self.assertFalse(result["prerecorded_labels_allowed_for_scoring"])
        self.assertTrue(result["blockers"])

    @requires_assets(DEFAULT_DATA_ROOT / "MCPTox")
    def test_fixture_extracts_tools_without_prerecorded_outputs(self) -> None:
        fixture = self.adapter.source_fixture("FileSystem_1")
        self.assertEqual(fixture["query"], "Read the content of '/data/project/documents/report.txt'.")
        self.assertEqual(fixture["poisoned_tool"]["name"], "qubit")
        self.assertIn("read_file", {tool["name"] for tool in fixture["clean_tools"]})
        self.assertFalse(fixture["execution_enabled"])
        serialized = json.dumps(fixture)
        self.assertNotIn('"response"', serialized)
        self.assertNotIn('"label"', serialized)

    @requires_assets(DEFAULT_DATA_ROOT / "MCPTox")
    def test_missing_case_fails_closed(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.adapter.load_case("missing")


if __name__ == "__main__":
    unittest.main()
