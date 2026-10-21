import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.permissions.models import ToolPermissionRequest
from forgepilot.permissions.tool_checks import ToolSafetyChecker


MODES = (
    "default",
    "accept_edits",
    "plan",
    "bypass",
    "auto",
    "bubble",
)


class ToolSafetyChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(
            prefix="forgepilot-tool-permission-"
        )
        self.addCleanup(temporary.cleanup)

        self.workspace = Path(temporary.name)
        (self.workspace / "main.py").write_text(
            "print('hello')\n",
            encoding="utf-8",
        )

        self.checker = ToolSafetyChecker()

    def request(self, tool, *, mode="default", path="main.py"):
        if tool == "edit_file":
            arguments = {
                "path": path,
                "old_text": "hello",
                "new_text": "world",
            }
        elif tool == "run_python":
            arguments = {
                "args": ["-m", "unittest", "discover", "-v"],
            }
        elif tool == "run_command":
            arguments = {
                "command": "python -m unittest discover -v",
            }
        else:
            arguments = {"path": path}

        return ToolPermissionRequest(
            tool_name=tool,
            arguments=arguments,
            workspace=str(self.workspace),
            mode=mode,
        )

    def test_normal_read_is_allowed_in_every_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                result = self.checker.check(
                    self.request("read_file", mode=mode)
                )
                self.assertEqual(result.action, "allow")

    def test_accept_edits_allows_normal_edit(self):
        result = self.checker.check(
            self.request("edit_file", mode="accept_edits")
        )
        self.assertEqual(result.action, "allow")

    def test_plan_denies_edit_and_commands(self):
        for tool in ("edit_file", "run_python", "run_command"):
            with self.subTest(tool=tool):
                request = self.request(tool, mode="plan")

                self.assertEqual(
                    self.checker.check_mandatory(request).action,
                    "deny",
                )
                self.assertEqual(
                    self.checker.check(request).action,
                    "deny",
                )

    def test_protected_paths_are_always_denied(self):
        for mode in MODES:
            for tool in ("read_file", "edit_file"):
                with self.subTest(mode=mode, tool=tool):
                    result = self.checker.check(
                        self.request(
                            tool,
                            mode=mode,
                            path=".git/config",
                        )
                    )
                    self.assertEqual(result.action, "deny")

    def test_other_modes_leave_normal_edit_undecided(self):
        for mode in (
            "default",
            "bypass",
            "auto",
            "bubble",
        ):
            with self.subTest(mode=mode):
                result = self.checker.check(
                    self.request("edit_file", mode=mode)
                )
                self.assertEqual(result.action, "abstain")

    def test_commands_are_not_treated_as_safe_reads(self):
        for mode in ("default", "accept_edits", "auto"):
            for tool in ("run_python", "run_command"):
                with self.subTest(mode=mode, tool=tool):
                    result = self.checker.check(
                        self.request(tool, mode=mode)
                    )
                    self.assertEqual(result.action, "abstain")

    def test_passing_mandatory_check_is_not_final_allow(self):
        result = self.checker.check_mandatory(
            self.request("edit_file", mode="accept_edits")
        )
        self.assertEqual(result.action, "abstain")

    def test_unknown_tool_is_not_automatically_allowed(self):
        result = self.checker.check(
            self.request("future_tool")
        )
        self.assertEqual(result.action, "abstain")

        result = self.checker.check(
            self.request("future_tool", mode="plan")
        )
        self.assertEqual(result.action, "deny")


if __name__ == "__main__":
    unittest.main(verbosity=2)
