from __future__ import annotations

from pathlib import Path
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root, runtime_python

from product_eval.toolemu import ToolEmuAdapter


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TOOLEMU_ROOT = default_data_root() / "ToolEmu-main"


class ToolEmuAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ToolEmuAdapter(TOOLEMU_ROOT)

    @requires_assets(TOOLEMU_ROOT)
    def test_preflight_locks_complete_source_contract(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(result["case_count"], 144)
        self.assertEqual(result["toolkit_count"], 38)
        self.assertEqual(result["tool_count"], 330)
        self.assertEqual(result["unique_tool_count"], 330)
        self.assertEqual(result["license"], "Apache-2.0")
        self.assertEqual(result["qualification_status"], "blocked_runtime_adapter")
        self.assertFalse(result["formal_release_ready"])
        self.assertFalse(result["prerecorded_labels_allowed_for_scoring"])

    @requires_assets(TOOLEMU_ROOT)
    def test_case_tools_map_to_strict_mcp_json_schemas(self) -> None:
        case = self.adapter.load_case("official_2")
        definitions = self.adapter.tool_definitions(case)
        self.assertEqual(case["Toolkits"], ["AugustSmartLock", "Gmail"])
        self.assertEqual(
            self.adapter.allowed_tool_names(case),
            {definition["name"] for definition in definitions},
        )
        self.assertTrue(all(tool["name"].startswith(("AugustSmartLock", "Gmail")) for tool in definitions))
        for tool in definitions:
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertTrue(set(schema.get("required", ())).issubset(schema["properties"]))

    @requires_assets(TOOLEMU_ROOT)
    def test_product_prompt_preserves_user_instruction_and_route_boundary(self) -> None:
        case = self.adapter.load_case("official_0")
        prompt = self.adapter.build_product_prompt(case)
        self.assertIn(case["User Instruction"], prompt)
        self.assertIn("MCP server named `toolemu`", prompt)


if __name__ == "__main__":
    unittest.main()
