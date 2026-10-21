import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.fork.models import ForkTasksInput
from forgepilot.fork.service import ForkService
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.fork.store import ForkJobStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls


class ForkServiceTests(unittest.TestCase):
    def setup_service(self, root):
        parent = SessionStore(root / "parent.jsonl")
        parent.create(SessionHeader(project_id="demo", owner_id="user", cwd=str(root)))
        messages = [{"role": "user", "content": "分析项目"}]
        parent.append_message(messages[0])
        snapshot = ForkSnapshot.capture(request={"model": "demo", "messages": messages}, messages=messages)
        params = ForkTasksInput.model_validate({"tasks": [
            {"name": "explore", "instruction": "查找测试", "permission_mode": "plan"},
        ]})
        parent.append_message({"role": "assistant", "type": "tool_call", "id": "fork_a",
                               "name": "fork_tasks", "arguments": params.model_dump()})
        jobs = ForkJobStore(root / "fork")
        return ForkService(jobs=jobs, parent_session=parent), params, snapshot

    def test_prepare_and_reopen_reuses_job_without_changing_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service, params, snapshot = self.setup_service(root)
            before = service.parent_session.path.read_bytes()
            job = service.prepare(params, ToolContext(root), tool_call_id="fork_a", snapshot=snapshot)
            self.assertEqual(job.tasks[0].status, "pending")
            child = service.jobs.get_session(job.id, job.tasks[0].id)
            self.assertEqual([entry.payload for entry in child.get_current_branch()], [
                {"role": "user", "content": "分析项目"},
                {"role": "user", "content": "查找测试"},
            ])
            reopened = ForkService(jobs=ForkJobStore(root / "fork"),
                                   parent_session=SessionStore(root / "parent.jsonl"))
            again = reopened.prepare(params, ToolContext(root), tool_call_id="fork_a")
            self.assertEqual(again.id, job.id)
            self.assertEqual(again.tasks[0].session_id, job.tasks[0].session_id)
            self.assertEqual(service.parent_session.path.read_bytes(), before)
            self.assertEqual(pending_tool_calls(service.parent_session.get_current_branch()), {"fork_a"})

    def test_rejects_unrecorded_or_changed_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service, params, snapshot = self.setup_service(root)
            with self.assertRaises(ValueError):
                service.prepare(params, ToolContext(root), tool_call_id="missing", snapshot=snapshot)
            changed = ForkTasksInput.model_validate({"tasks": [{"name": "other", "instruction": "改代码"}]})
            with self.assertRaises(ValueError):
                service.prepare(changed, ToolContext(root), tool_call_id="fork_a", snapshot=snapshot)
            with self.assertRaises(ValueError):
                service.prepare(params, ToolContext(root / "other"), tool_call_id="fork_a", snapshot=snapshot)
            self.assertFalse(service.jobs.root.exists())


if __name__ == "__main__":
    unittest.main()
