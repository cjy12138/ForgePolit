import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.fork.dispatch import ForkDispatcher
from forgepilot.fork.executor import ForkExecutor
from forgepilot.fork.service import ForkService
from forgepilot.fork.store import ForkJobStore
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.fork import build_fork_tools


class ChildModel:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"type": "tool_call", "id": "child_read", "name": "read_file",
                    "arguments": {"path": "README.md"}}
        assert any(message.get("role") == "tool" and "示例代码库" in message["content"]
                   for message in messages)
        return {"type": "final", "content": "README.md 说明这是示例代码库。"}


class ForkDispatchTests(unittest.TestCase):
    def test_parent_tool_dispatches_child_and_receives_only_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("示例代码库", encoding="utf-8")
            session = SessionStore(root / "parent.jsonl")
            header = SessionHeader(project_id="demo", owner_id="user", cwd=str(root))
            session.create(header)
            requests = []

            def create(**request):
                requests.append(request)
                if len(requests) == 1:
                    call = SimpleNamespace(id="parent_fork", function=SimpleNamespace(
                        name="fork_tasks", arguments=json.dumps({"tasks": [{
                            "name": "explore", "instruction": "读取 README.md", "base_tools": ["read_file"],
                        }]}),
                    ))
                    message = SimpleNamespace(tool_calls=[call], content=None)
                    finish = "tool_calls"
                else:
                    result = json.loads(json.loads(request["messages"][-1]["content"])["output"])
                    self.assertEqual(result["status"], "completed")
                    self.assertIn("示例代码库", result["summary"])
                    message = SimpleNamespace(tool_calls=None, content="子任务已完成。")
                    finish = "stop"
                return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=message, finish_reason=finish)])

            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            parent_model = DeepSeekModel(client)
            jobs = ForkJobStore(root / "fork")
            child_model = ChildModel()
            child_tools = ToolRegistry(build_file_tools())
            executor = ForkExecutor(jobs=jobs, create_agent=lambda child, snapshot, task: Agent(
                child_model, child_tools, ToolContext(root), session_store=child,
            ))
            dispatcher = ForkDispatcher(service=ForkService(jobs=jobs, parent_session=session),
                                        executor=executor, parent_model=parent_model)
            registry = ToolRegistry(build_fork_tools(dispatch=dispatcher))
            registry.configure_permissions(mode="bypass")
            parent = Agent(parent_model, registry, ToolContext(root), session_store=session)
            result = parent.run("委派读取 README.md，再报告结论")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(requests), 2)
            job = jobs.find_by_call(header.session_id, "parent_fork")
            self.assertEqual(job.tasks[0].status, "completed")
            self.assertEqual(pending_tool_calls(session.get_current_branch()), set())
            self.assertFalse(any(entry.payload.get("name") == "read_file" for entry in session.get_current_branch()))
            self.assertTrue(any(entry.payload.get("name") == "read_file"
                                for entry in jobs.get_session(job.id, job.tasks[0].id).get_current_branch()))
            self.assertEqual(child_model.calls, 2)


if __name__ == "__main__":
    unittest.main()
