"""验证 bugfix 的权限接入和恢复；使用模拟运行环境，不启动 Docker 或 API。"""
import unittest
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from forgepilot.bugfix_service import BugfixService
from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.environments.bugfix_runtime import BugfixToolRegistry
from forgepilot.permissions.command_checks import check_command_permissions
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import ApprovalDecision, PermissionDecision, PermissionSettings
from forgepilot.permissions.service import PermissionService
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.tools.sandbox_commands import RunCommandInput


class CommandModel:
    max_output_tokens = 2048

    def __init__(self, command):
        self.command = command
        self.calls = 0

    def estimate_input_tokens(self, *, messages, tools):
        return 1000

    def generate(self, *, messages, tools):
        self.calls += 1
        if self.calls % 2:
            return {
                "type": "tool_call", "id": f"call_{self.calls}",
                "name": "run_command", "arguments": {"command": self.command},
            }
        return {"type": "final", "content": "模拟任务结束。"}


class BugfixPermissionChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-bugfix-permissions-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        task_path = self.root / "tasks" / uuid4().hex
        task_path.mkdir(parents=True)
        self.task = SimpleNamespace(
            id=task_path.name, path=task_path, baseline_commit_id="a" * 40,
            source_workspace=self.source,
        )
        self.executed = []
        self.approvals = []
        self.runtimes = []

        def start_runtime(**kwargs):
            environment = SimpleNamespace(container_id="fake-container")

            def handler(*, command, timeout_seconds, context):
                self.executed.append((command, context.task, context.workspace))
                return ToolResult(ok=True, output="simulated output")

            tools = BugfixToolRegistry(environment, {"run_command": {
                "name": "run_command", "description": "mock command",
                "input_model": RunCommandInput, "handler": handler,
                "check_permissions": check_command_permissions,
            }})
            runtime = SimpleNamespace(
                task=self.task, environment=environment, tools=tools,
                context=ToolContext(workspace=self.task.path), close=lambda: None,
            )
            self.runtimes.append(runtime)
            return runtime

        self.start_patch = patch("forgepilot.bugfix_service.start_bugfix_runtime", start_runtime)
        self.start_patch.start()
        self.addCleanup(self.start_patch.stop)
        export_patch = patch("forgepilot.bugfix_service.export_bugfix_result", return_value={})
        export_patch.start()
        self.addCleanup(export_patch.stop)

    def permission_service(self, mode, reviewer=None):
        def approve(request, checked):
            self.approvals.append(request)
            return PermissionDecision(action="allow", source="parent", reason="模拟用户批准")
        return PermissionService(
            PermissionEngine(PermissionSettings(mode=mode)),
            reviewer=reviewer, approval=approve,
        )

    def service(self, mode, command="echo hello", *, max_steps=3, reviewer=None):
        return BugfixService(
            model=CommandModel(command), source=self.source,
            tasks_root=self.root / "tasks",
            budget=ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=2000),
            max_steps=max_steps, permission_service=self.permission_service(mode, reviewer),
        )

    def run_silently(self, service):
        with redirect_stdout(StringIO()):
            return service.run("输出 hello，不修改代码。")

    def test_default_approval_receives_bugfix_ticket_and_workspace(self):
        result = self.run_silently(self.service("default"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.approvals), 1)
        self.assertIn("输出 hello，不修改代码。", self.approvals[0].task)
        self.assertEqual(Path(self.approvals[0].workspace), self.task.path)
        self.assertEqual(len(self.executed), 1)
        audit_path = Path(result["run_dir"]) / "permissions.jsonl"
        audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(record["task_id"] == self.task.id for record in audit))
        self.assertTrue(audit[-1]["executed"])

    def test_bypass_and_plan_do_not_skip_mandatory_checks(self):
        for mode, command in (("bypass", "cat .env"), ("plan", "echo hello")):
            with self.subTest(mode=mode):
                # 每个任务使用独立的会话和状态。
                self.task.id = uuid4().hex
                self.task.path = self.root / "tasks" / self.task.id
                self.task.path.mkdir()
                self.run_silently(self.service(mode, command))
        self.assertEqual(self.executed, [])
        self.assertEqual(self.approvals, [])

    def test_auto_uses_reviewer_without_prompting(self):
        reviewed = []

        class Reviewer:
            def review(self, request):
                reviewed.append(request)
                return PermissionDecision(action="allow", source="reviewer", reason="模拟 AI 批准")

        self.run_silently(self.service("auto", reviewer=Reviewer()))
        self.assertEqual(len(reviewed), 1)
        self.assertIn("输出 hello", reviewed[0].task)
        self.assertEqual(self.approvals, [])
        self.assertEqual(len(self.executed), 1)

    def test_resume_uses_current_policy_and_original_task(self):
        first = self.run_silently(self.service("bypass", max_steps=1))
        self.assertEqual(first["status"], "step_limit")
        self.assertEqual(len(self.executed), 1)
        saved = SimpleNamespace(
            task=self.task, run_dir=Path(first["run_dir"]), image="python:3.13-slim",
            ticket="输出 hello，不修改代码。",
        )
        resumed = self.service("plan")
        with patch("forgepilot.bugfix_service.load_bugfix_task", return_value=saved):
            with redirect_stdout(StringIO()):
                resumed.resume(self.task.id)
        self.assertEqual(self.runtimes[-1].tools.permission_mode, "plan")
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(self.approvals, [])

    def test_resume_reuses_only_its_saved_session_grant(self):
        first_service = self.service("default", max_steps=1)
        first_service.permission_service.approval = lambda request, checked: ApprovalDecision(
            action="allow", source="parent", reason="模拟当前会话批准", remember_session=True,
        )
        first = self.run_silently(first_service)
        run_dir = Path(first["run_dir"])
        self.assertTrue((run_dir / "permission-state.json").is_file())
        saved = SimpleNamespace(
            task=self.task, run_dir=run_dir, image="python:3.13-slim", ticket="输出 hello，不修改代码。",
        )
        resumed = self.service("default")
        with patch("forgepilot.bugfix_service.load_bugfix_task", return_value=saved):
            with redirect_stdout(StringIO()):
                resumed.resume(self.task.id)
        self.assertEqual(len(self.executed), 2)
        self.assertEqual(self.approvals, [])

    def test_new_bugfix_task_does_not_inherit_previous_task_grant(self):
        service = self.service("default")
        asked = []

        def approve(request, checked):
            asked.append(request)
            return ApprovalDecision(
                action="allow" if len(asked) == 1 else "deny",
                source="parent", reason="模拟用户选择", remember_session=len(asked) == 1,
            )

        service.permission_service.approval = approve
        self.run_silently(service)
        self.task.id = uuid4().hex
        self.task.path = self.root / "tasks" / self.task.id
        self.task.path.mkdir()
        self.run_silently(service)
        self.assertEqual(len(asked), 2)
        self.assertEqual(len(self.executed), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
