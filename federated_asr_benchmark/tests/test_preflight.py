from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from tools.preflight import command_version


class PreflightCommandVersionTests(unittest.TestCase):
    @patch("tools.preflight.subprocess.run")
    @patch("tools.preflight.shutil.which", return_value=r"C:\\Program Files\\nodejs\\npm.cmd")
    def test_resolves_windows_command_shim(self, _which, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="11.12.1\n", stderr="")

        version, error = command_version(["npm", "--version"])

        self.assertEqual(version, "11.12.1")
        self.assertIsNone(error)
        self.assertEqual(
            run.call_args.args[0],
            [r"C:\\Program Files\\nodejs\\npm.cmd", "--version"],
        )

    @patch("tools.preflight.subprocess.run")
    @patch("tools.preflight.shutil.which", return_value=None)
    def test_missing_command_fails_without_launch(self, _which, run) -> None:
        version, error = command_version(["npm", "--version"])

        self.assertIsNone(version)
        self.assertEqual(error, "executable not found: npm")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
