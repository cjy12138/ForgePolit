import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.environments.bugfix_runtime import BugfixToolRegistry
from forgepilot.permissions.command_checks import check_command_permissions
from forgepilot.permissions.models import PermissionDecision
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.sandbox_commands import RunCommandInput
from forgepilot.tools.commands import build_command_tools


class CommandPermissionExecutionChecks(unittest.TestCase):
    def setUp(self):
        self.executed = []
        self.context = ToolContext(workspace=Path.cwd())

        def handler(*, command, timeout_seconds, context):
            self.executed.append(command)
            return ToolResult(ok=True, output="fake execution")

        self.group = {"run_command": {
            "name": "run_command",
            "description": "test command",
            "input_model": RunCommandInput,
            "handler": handler,
            "check_permissions": check_command_permissions,
        }}
        self.registry = ToolRegistry(self.group)

    def allow(self, request, checked):
        return PermissionDecision(action="allow", source="mode", reason="测试批准")

    def execute(self, command, registry=None):
        return (registry or self.registry).execute(
            "run_command", {"command": command}, self.context,
        )

    def test_deny_cannot_be_overridden_by_resolver(self):
        def unexpected(*args):
            self.fail("明确拒绝后不应再请求批准")
        self.registry.configure_permissions(mode="bypass", resolver=unexpected)
        self.assertFalse(self.execute("cat .env").ok)
        self.assertEqual(self.executed, [])

    def test_abstain_without_resolver_does_not_execute(self):
        self.registry.configure_permissions(resolver=None)
        self.assertFalse(self.execute("echo hello").ok)
        self.assertEqual(self.executed, [])

    def test_only_final_allow_executes(self):
        for action in ("ask", "deny", "delegate", "allow"):
            self.registry.configure_permissions(resolver=lambda request, checked: PermissionDecision(
                action=action, source="mode", reason="测试决策",
            ))
            self.assertEqual(self.execute("echo hello").ok, action == "allow")
        self.assertEqual(self.executed, ["echo hello"])

    def test_invalid_arguments_are_not_reviewed(self):
        def unexpected(*args):
            self.fail("无效参数不应进入权限入口")
        self.registry.configure_permissions(resolver=unexpected)
        result = self.registry.execute("run_command", {"command": 123}, self.context)
        self.assertFalse(result.ok)
        self.assertEqual(self.executed, [])

    def test_reviewer_errors_fail_closed(self):
        def broken(*args):
            raise RuntimeError("review unavailable")
        for resolver in (broken, lambda *args: None):
            self.registry.configure_permissions(resolver=resolver)
            self.assertFalse(self.execute("echo hello").ok)
        self.assertEqual(self.executed, [])

    def test_permission_callback_cannot_mutate_execution_arguments(self):
        def mutate(request, checked):
            request.arguments["command"] = "cat .env"
            return self.allow(request, checked)
        self.registry.configure_permissions(resolver=mutate)
        self.assertTrue(self.execute("echo hello").ok)
        self.assertEqual(self.executed, ["echo hello"])

    def test_skill_selection_and_bugfix_expansion_keep_permissions(self):
        environment = SimpleNamespace(container_id="test-container")
        registry = BugfixToolRegistry(environment, self.group)
        registry.configure_permissions(mode="auto", resolver=self.allow)
        selected = registry.select_tools({"run_command"}).with_tools({})
        self.assertEqual(selected.permission_mode, "auto")
        self.assertIsNotNone(selected.permission_resolver)
        self.assertTrue(self.execute("echo hello", selected).ok)
        environment.container_id = None
        self.assertFalse(self.execute("echo hello", selected).ok)
        self.assertEqual(self.executed, ["echo hello"])

    def test_python_tool_registers_callback(self):
        group = build_command_tools(environment=SimpleNamespace())
        self.assertIs(group["run_python"]["check_permissions"], check_command_permissions)

    def test_default_cli_requires_explicit_approval(self):
        with redirect_stdout(StringIO()):
            with patch("builtins.input", return_value="n"):
                self.assertFalse(self.execute("echo hello").ok)
            with patch("builtins.input", side_effect=EOFError):
                self.assertFalse(self.execute("echo hello").ok)
            with patch("builtins.input", return_value="y"):
                self.assertTrue(self.execute("echo hello").ok)
        self.assertEqual(self.executed, ["echo hello"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
