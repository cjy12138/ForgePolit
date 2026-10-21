import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.fork.factory import ForkAgentFactory
from forgepilot.fork.models import ForkTaskInput, ForkTaskState
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.permissions.models import PermissionRule, PermissionSettings, RuleLayer, ToolPermissionRequest
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.service import PermissionService
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.fork import build_fork_tools


class ForkFactoryTests(unittest.TestCase):
    def setup_factory(self, root, mode="default"):
        tools = ToolRegistry(build_file_tools(), build_fork_tools(dispatch=lambda *_: None))
        tools.configure_permissions(service=PermissionService(PermissionEngine(PermissionSettings(
            mode=mode, layers=[RuleLayer(source="project", rules=[
                PermissionRule(action="deny", tool="read_file", pattern="secret.txt"),
            ])],
        ))))
        messages = [{"role": "user", "content": "父任务"}]
        model = DeepSeekModel(client=None)
        snapshot = ForkSnapshot.capture(request=model.prepare_request(messages, tools.definitions()), messages=messages)
        session = SessionStore(root / "session.jsonl")
        session.create(SessionHeader(project_id="demo", owner_id="user", cwd=str(root)))
        session.append_message(messages[0])
        session.append_message({"role": "user", "content": "子任务"})
        factory = ForkAgentFactory(client=None, parent_tools=tools, context_window=32000)
        return factory, session, snapshot, tools

    def test_preserves_request_prefix_and_enforces_inherited_rules(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, parent = self.setup_factory(root)
            task = ForkTaskState(task=ForkTaskInput(name="explore", instruction="子任务"))
            agent = factory(session, snapshot, task)
            messages = [entry.payload for entry in session.get_current_branch()]
            request = agent.model.prepare_request(messages, agent.tool_definitions)
            original = snapshot.request()
            self.assertEqual(request["messages"][:len(original["messages"])], original["messages"])
            self.assertEqual({item["function"]["name"] for item in request["tools"]},
                             {"read_file", "list_files"})
            self.assertNotIn("fork_tasks", {item["name"] for item in agent.tool_definitions})
            denied = agent.tools.permission_service.authorize(ToolPermissionRequest(
                tool_name="read_file", arguments={"path": "secret.txt"}, workspace=str(root),
            ))
            self.assertEqual(denied.action, "deny")
            self.assertEqual(agent.tools.permission_service.engine.settings.mode, "default")
            self.assertEqual(parent.permission_service.engine.settings.mode, "default")
            self.assertFalse(agent.tools.execute("fork_tasks", {}, ToolContext(root)).ok)

    def test_mode_change_cannot_escalate(self):
        with TemporaryDirectory() as directory:
            factory, session, snapshot, parent = self.setup_factory(Path(directory))
            plan = ForkTaskState(task=ForkTaskInput(name="explore", instruction="子任务", permission_mode="plan"))
            self.assertEqual(factory(session, snapshot, plan).tools.permission_mode, "plan")
            bypass = ForkTaskState(task=ForkTaskInput(name="explore", instruction="子任务", permission_mode="bypass"))
            with self.assertRaises(ValueError):
                factory(session, snapshot, bypass)
            self.assertEqual(parent.permission_mode, "default")
            messages = [{"role": "user", "content": "被修改的历史"}]
            compressed = factory(session, snapshot, plan).model.prepare_request(messages, [])
            self.assertEqual(compressed["messages"], messages)
            self.assertEqual(compressed["tools"], [])

    def test_only_requested_base_tools_are_available(self):
        with TemporaryDirectory() as directory:
            factory, session, snapshot, _ = self.setup_factory(Path(directory))
            task = ForkTaskState(task=ForkTaskInput(
                name="explore", instruction="子任务", base_tools=["read_file"],
            ))
            agent = factory(session, snapshot, task)
            self.assertEqual([tool["name"] for tool in agent.tool_definitions], ["read_file"])
            unavailable = ForkTaskState(task=ForkTaskInput(
                name="explore", instruction="子任务", base_tools=["missing"],
            ))
            with self.assertRaises(ValueError):
                factory(session, snapshot, unavailable)
            for names in (["fork_tasks"], ["read_file", "read_file"], [""]):
                with self.assertRaises(ValueError):
                    ForkTaskInput(name="explore", instruction="子任务", base_tools=names)


if __name__ == "__main__":
    unittest.main()
