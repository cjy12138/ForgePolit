import unittest
from pathlib import Path

from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.permissions.models import PermissionDecision
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.fork import build_fork_tools


class ForkToolTests(unittest.TestCase):
    def build_registry(self, dispatch, action="allow"):
        registry = ToolRegistry(build_fork_tools(dispatch=dispatch))
        registry.configure_permissions(resolver=lambda *_: PermissionDecision(
            action=action, source="parent", reason="离线检查授权",
        ))
        return registry

    def test_schema_and_dispatch(self):
        received = []

        def dispatch(params, context):
            received.append((params, context))
            return ToolResult(ok=False, output="子任务中断")

        registry = self.build_registry(dispatch)
        schema = registry.definitions()[0]
        self.assertEqual(schema["name"], "fork_tasks")
        self.assertEqual(schema["parameters"]["properties"]["tasks"]["maxItems"], 1)
        context = ToolContext(workspace=Path.cwd(), task="主任务")
        result = registry.execute("fork_tasks", {"tasks": [
            {"name": "explore", "instruction": "分析代码", "permission_mode": "plan"},
        ]}, context)
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "子任务中断")
        self.assertEqual(received[0][0].tasks[0].permission_mode, "plan")
        self.assertEqual(received[0][1], context)

    def test_invalid_input_and_denial_never_dispatch(self):
        received = []

        def dispatch(params, context):
            received.append(params)
            return ToolResult(ok=True, output="完成")

        context = ToolContext(workspace=Path.cwd())
        registry = self.build_registry(dispatch)
        for tasks in ([], [
            {"name": "first", "instruction": "A"},
            {"name": "second", "instruction": "B"},
        ], [{"name": "a", "instruction": "A", "permission_mode": "invalid"}]):
            self.assertFalse(registry.execute("fork_tasks", {"tasks": tasks}, context).ok)
        denied = self.build_registry(dispatch, action="deny")
        self.assertFalse(denied.execute("fork_tasks", {"tasks": [
            {"name": "a", "instruction": "A"},
        ]}, context).ok)
        self.assertEqual(received, [])

    def test_plan_can_only_delegate_plan_tasks(self):
        received = []
        def dispatch(params, context):
            received.append(params)
            return ToolResult(ok=True, output="完成")
        registry = ToolRegistry(build_fork_tools(dispatch=dispatch))
        registry.configure_permissions(mode="plan")
        context = ToolContext(workspace=Path.cwd())
        self.assertTrue(registry.execute("fork_tasks", {"tasks": [
            {"name": "read", "instruction": "读取", "permission_mode": "plan"},
        ]}, context).ok)
        self.assertFalse(registry.execute("fork_tasks", {"tasks": [
            {"name": "write", "instruction": "修改", "permission_mode": "bypass"},
        ]}, context).ok)
        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
