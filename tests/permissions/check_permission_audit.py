"""验证审计与执行一致，并且自由文本、凭证不会复制到日志。"""
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.environments.bugfix_runtime import BugfixToolRegistry
from forgepilot.permissions.audit import PermissionAuditLog
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.sandbox_commands import RunCommandInput


class PermissionAuditChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-permission-audit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "logs" / "permissions.jsonl"
        self.audit = PermissionAuditLog(self.path, session_id="session-one")
        self.context = ToolContext(workspace=self.root, task="private task body")
        self.executions = 0
        self.handler_result = ToolResult(ok=True, output="private output")

        def handler(**kwargs):
            self.executions += 1
            return self.handler_result

        self.group = {"run_command": {
            "name": "run_command", "description": "fake command",
            "input_model": RunCommandInput, "handler": handler,
        }}
        self.registry = ToolRegistry(self.group)
        self.registry.configure_permissions(mode="bypass")
        self.registry.configure_audit(self.audit)

    def records(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def execute(self, command="echo hello", registry=None):
        return (registry or self.registry).execute(
            "run_command", {"command": command}, self.context,
        )

    def test_decision_precedes_execution_and_success_is_recorded(self):
        self.assertTrue(self.execute().ok)
        records = self.records()
        self.assertEqual([record["event"] for record in records], [
            "request", "decision", "execution_started", "result",
        ])
        self.assertEqual(len({record["call_id"] for record in records}), 1)
        self.assertEqual(records[1]["action"], "allow")
        self.assertEqual(records[1]["source"], "mode")
        self.assertEqual(records[-1]["status"], "succeeded")
        self.assertTrue(records[-1]["executed"])

    def test_rejection_does_not_claim_execution(self):
        self.assertFalse(self.execute("cat .env").ok)
        records = self.records()
        self.assertEqual([record["event"] for record in records], ["request", "decision", "result"])
        self.assertEqual(records[1]["action"], "deny")
        self.assertEqual(records[-1]["status"], "not_executed")
        self.assertFalse(records[-1]["executed"])
        self.assertEqual(self.executions, 0)

    def test_failed_process_exit_is_not_logged_as_success(self):
        self.handler_result = ToolResult(ok=True, output="test failure", exit_code=1)
        self.execute()
        record = self.records()[-1]
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["tool_ok"])
        self.assertEqual(record["exit_code"], 1)

    def test_unknown_tool_and_invalid_arguments_are_audited(self):
        self.registry.execute("missing", {"api_key": "top-secret-value"}, self.context)
        self.registry.execute("run_command", {"command": 123}, self.context)
        decisions = [record for record in self.records() if record["event"] == "decision"]
        self.assertEqual([record["reason_code"] for record in decisions], ["unknown_tool", "invalid_arguments"])
        self.assertNotIn("top-secret-value", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.executions, 0)

    def test_no_task_command_reason_or_output_body_is_copied(self):
        self.execute("echo secret-command-body")
        text = self.path.read_text(encoding="utf-8")
        for value in ("private task body", "secret-command-body", "private output", "bypass 模式跳过后续询问"):
            self.assertNotIn(value, text)
        records = self.records()
        self.assertTrue(records[0]["task"]["redacted"])
        self.assertIn("sha256", records[0]["arguments"]["command"])
        self.assertIn("sha256", records[1]["reason"])

    def test_keyboard_interrupt_is_not_mistaken_for_success(self):
        def interrupt(**kwargs):
            raise KeyboardInterrupt
        self.registry._tools["run_command"]["handler"] = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        record = self.records()[-1]
        self.assertEqual(record["status"], "interrupted")
        self.assertTrue(record["executed"])

    def test_audit_failure_does_not_rerun_or_approve_rejected_tool(self):
        with patch.object(self.audit, "append", side_effect=OSError("disk unavailable")):
            output = StringIO()
            with redirect_stdout(output):
                self.assertTrue(self.execute().ok)
                self.assertFalse(self.execute("cat .env").ok)
            self.assertEqual(output.getvalue().count("权限审计日志写入失败"), 1)
        self.assertEqual(self.executions, 1)

    def test_selection_expansion_stopped_container_and_reopen(self):
        environment = SimpleNamespace(container_id="fake-container")
        registry = BugfixToolRegistry(environment, self.group)
        registry.configure_permissions(mode="bypass")
        registry.configure_audit(self.audit)
        selected = registry.select_tools({"run_command"}).with_tools({})
        self.assertIs(selected.audit_log, self.audit)
        self.execute(registry=selected)
        environment.container_id = None
        self.assertFalse(self.execute(registry=selected).ok)
        records = self.records()
        self.assertEqual(records[-2]["reason_code"], "container_stopped")
        self.assertFalse(records[-1]["executed"])
        reopened = PermissionAuditLog(self.path, session_id="session-one")
        reopened.append(call_id="another", event="reopen_check")
        self.assertEqual(self.records()[:-1], records)


if __name__ == "__main__":
    unittest.main(verbosity=2)
