import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.exceptions import ModelError
from forgepilot.fork.dispatch import ForkDispatcher
from forgepilot.fork.executor import ForkExecutor
from forgepilot.fork.models import ForkTasksInput
from forgepilot.fork.service import ForkService
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.fork.store import ForkJobStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls
from forgepilot.tool_registry import ToolRegistry


class FinalModel:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools):
        self.calls += 1
        return {"type": "final", "content": "恢复后完成分析"}


class ForkRecoveryTests(unittest.TestCase):
    def prepare(self, root):
        parent = SessionStore(root / "parent.jsonl")
        parent.create(SessionHeader(project_id="demo", owner_id="user", cwd=str(root)))
        parent.append_message({"role": "user", "content": "探索代码"})
        params = ForkTasksInput.model_validate({"tasks": [{"name": "explore", "instruction": "探索"}]})
        parent.append_message({"role": "assistant", "type": "tool_call", "id": "fork_a",
                               "name": "fork_tasks", "arguments": params.model_dump()})
        jobs = ForkJobStore(root / "fork")
        service = ForkService(jobs=jobs, parent_session=parent)
        job = service.prepare(params, ToolContext(root), tool_call_id="fork_a",
                              snapshot=ForkSnapshot.capture(request={}, messages=[]))
        model = FinalModel()
        executor = ForkExecutor(jobs=jobs, create_agent=lambda session, snapshot, task: Agent(
            model, ToolRegistry(), ToolContext(root), session_store=session,
        ))
        permissions = ToolRegistry()
        permissions.configure_permissions(mode="bypass")
        dispatcher = ForkDispatcher(service=service, executor=executor, parent_model=None,
                                    recovery_permissions=permissions.permission_service)
        return dispatcher, parent, jobs, job, model

    def test_hard_stop_after_final_does_not_repeat_model_or_tool_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dispatch, parent, jobs, job, model = self.prepare(root)
            task = job.tasks[0]
            jobs.transition(job.id, task.id, status="running")
            jobs.get_session(job.id, task.id).append_message({
                "role": "assistant", "type": "final", "content": "中断前已完成",
            })
            self.assertEqual(dispatch.recover_pending(ToolContext(root)), [job.id])
            self.assertEqual(model.calls, 0)
            self.assertEqual(jobs.get(job.id).tasks[0].summary, "中断前已完成")
            self.assertEqual(pending_tool_calls(parent.get_current_branch()), set())
            before = parent.path.read_bytes()
            self.assertEqual(dispatch.recover_pending(ToolContext(root)), [])
            self.assertEqual(parent.path.read_bytes(), before)

    def test_unknown_child_tool_requires_explicit_marking(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dispatch, parent, jobs, job, model = self.prepare(root)
            child = jobs.get_session(job.id, job.tasks[0].id)
            child.append_message({"role": "assistant", "type": "tool_call", "id": "unknown",
                                  "name": "run_python", "arguments": {"args": ["script.py"]}})
            with self.assertRaises(ModelError):
                dispatch.recover_pending(ToolContext(root))
            self.assertEqual(model.calls, 0)
            self.assertEqual(pending_tool_calls(parent.get_current_branch()), {"fork_a"})
            dispatch.recover_pending(ToolContext(root), mark_unknown=True)
            self.assertEqual(model.calls, 1)
            self.assertEqual(pending_tool_calls(child.get_current_branch()), set())
            self.assertEqual(pending_tool_calls(parent.get_current_branch()), set())

    def test_current_permission_denial_blocks_recovery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dispatch, parent, jobs, job, model = self.prepare(root)
            from forgepilot.permissions.engine import PermissionEngine
            from forgepilot.permissions.models import PermissionRule, PermissionSettings, RuleLayer
            from forgepilot.permissions.service import PermissionService
            dispatch.recovery_permissions = PermissionService(PermissionEngine(PermissionSettings(
                layers=[RuleLayer(source="project", rules=[PermissionRule(action="deny", tool="fork_tasks")])],
            )))
            before = parent.path.read_bytes()
            with self.assertRaises(ValueError):
                dispatch.recover_pending(ToolContext(root))
            self.assertEqual(model.calls, 0)
            self.assertEqual(parent.path.read_bytes(), before)

    def test_request_budget_survives_reopen(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, jobs, job, _ = self.prepare(root)
            task = job.tasks[0]
            jobs.transition(job.id, task.id, status="running")
            for _ in range(task.request_limit):
                jobs.reserve_request(job.id, task.id)
            with self.assertRaises(ModelError):
                ForkJobStore(jobs.root).reserve_request(job.id, task.id)
            self.assertEqual(jobs.get(job.id).tasks[0].model_requests, task.request_limit)


if __name__ == "__main__":
    unittest.main()
