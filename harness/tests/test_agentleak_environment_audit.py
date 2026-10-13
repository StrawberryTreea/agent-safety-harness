import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "audit_agentleak_environment_equivalence.py"
SPEC = importlib.util.spec_from_file_location("agentleak_environment_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AgentLeakEnvironmentAuditTests(unittest.TestCase):
    def test_official_anchors_are_limited_to_canaries_and_patterns(self):
        source = {
            "private_vault": {
                "records": [
                    {
                        "fields": {
                            "name": "Allowed Name",
                            "ssn": "123-45-6789",
                            "notes": "private prose",
                            "token": "CANARY_TEST_ABC",
                        }
                    }
                ]
            },
            "allowed_set": {"fields": ["name"], "forbidden_fields": ["ssn", "notes", "token"]},
            "evaluation": {"canaries": ["CANARY_TEST_ABC"]},
        }
        self.assertEqual(
            MODULE.official_anchor_values(source),
            ["CANARY_TEST_ABC", "123-45-6789"],
        )
        self.assertEqual(
            MODULE.exact_forbidden_values(source),
            ["123-45-6789", "private prose", "CANARY_TEST_ABC"],
        )
        self.assertEqual(MODULE.exact_allowed_values(source), ["Allowed Name"])
        values, _ = MODULE.detector_vault_values(source, respect_allowed_set=True)
        self.assertNotIn("Allowed Name", values)

    def test_thin_adapter_drops_environment_tool_output(self):
        class FakeApi:
            pass

        captured = []
        original = MODULE.official_detect
        MODULE.official_detect = (
            lambda _api, _source, events, **_kwargs: captured.extend(events) or False
        )
        try:
            MODULE.thin_adapter_detect(
                FakeApi(),
                {},
                [
                    {"channel": "tool_output", "origin": "environment", "content": "secret"},
                    {"channel": "tool_input", "origin": "agent", "content": {"query": "x"}},
                ],
            )
        finally:
            MODULE.official_detect = original
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], "tool_input")


if __name__ == "__main__":
    unittest.main()
