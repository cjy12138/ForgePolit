import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.fork.executor import ForkExecutor
from forgepilot.fork.models import ForkTaskInput
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.fork.store import ForkJobStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.tool_registry import ToolRegistry


class CountingModel:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools):
        self.calls += 1
        return {"type": "final", "content": "相关测试位于 tests/"}


class ForkExecutorTests(unittest.TestCase):
    def prepare(self, root):
        jobs = ForkJobStore(root / "fork")
        header = SessionHeader(project_id="demo", owner_id="user", cwd=str(root))
        job = jobs.create(parent_session_id=header.session_id, tool_call_id="fork_a", tasks=[
            ForkTaskInput(name="explore", instruction="寻找测试"),
        ])
        jobs.save_snapshot(job.id, ForkSnapshot.capture(request={}, messages=[]))
        jobs.initialize_session(job.id, job.tasks[0].id, parent_header=header, messages=[])
        return jobs, job

    def test_executes_real_agent_loop_and_reuses_completed_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            jobs, job = self.prepare(root)
            model = CountingModel()
            executor = ForkExecutor(jobs=jobs, create_agent=lambda session, snapshot, task: Agent(
                model, ToolRegistry(), ToolContext(root), session_store=session,
            ))
            result = executor.execute(job.id)
            self.assertTrue(result.ok)
            self.assertEqual(json.loads(result.output)["summary"], "相关测试位于 tests/")
            session = jobs.get_session(job.id, job.tasks[0].id)
            self.assertEqual(len(session.get_current_branch()), 2)
            self.assertEqual(session.get_current_branch()[-1].payload["type"], "final")
            self.assertEqual(executor.execute(job.id), result)
            self.assertEqual(model.calls, 1)

    def test_pending_tool_is_not_reexecuted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            jobs, job = self.prepare(root)
            task = job.tasks[0]
            session = jobs.get_session(job.id, task.id)
            session.append_message({"role": "assistant", "type": "tool_call", "id": "unknown",
                                    "name": "read_file", "arguments": {"path": "README.md"}})
            model = CountingModel()
            executor = ForkExecutor(jobs=jobs, create_agent=lambda session, snapshot, task: Agent(
                model, ToolRegistry(), ToolContext(root), session_store=session,
            ))
            self.assertFalse(executor.execute(job.id).ok)
            self.assertEqual(jobs.get(job.id).tasks[0].status, "interrupted")
            self.assertEqual(model.calls, 0)
            session.append_message({"role": "tool", "tool_call_id": "unknown", "content": "原执行结果未知",
                                    "is_error": True, "exit_code": None})
            self.assertTrue(executor.execute(job.id).ok)
            self.assertEqual(model.calls, 1)


if __name__ == "__main__":
    unittest.main()
