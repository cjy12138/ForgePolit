import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.fork.models import ForkTaskInput
from forgepilot.fork.store import ForkJobStore


class ForkStateTests(unittest.TestCase):
    def test_independent_tasks_and_reopen(self):
        with TemporaryDirectory() as directory:
            store = ForkJobStore(Path(directory))
            job = store.create(parent_session_id="parent", tool_call_id="call_a", tasks=[
                ForkTaskInput(name="code", instruction="分析调用关系", permission_mode="plan"),
            ])
            other = store.create(parent_session_id="parent", tool_call_id="call_b", tasks=[
                ForkTaskInput(name="tests", instruction="查找测试"),
            ])
            first, second = job.tasks[0], other.tasks[0]
            store.transition(job.id, first.id, status="running")
            store.transition(job.id, first.id, status="interrupted", error="程序退出")
            reopened = ForkJobStore(Path(directory))
            state = reopened.get(job.id)
            self.assertEqual(state.tasks[0].status, "interrupted")
            self.assertEqual(reopened.get(other.id).tasks[0].status, "pending")
            self.assertEqual(state.tasks[0].task.permission_mode, "plan")
            reopened.transition(job.id, first.id, status="running")
            reopened.transition(job.id, first.id, status="completed", summary="入口位于 app.py")
            with self.assertRaises(ValueError):
                reopened.transition(job.id, first.id, status="running")
            self.assertEqual(reopened.get(other.id).tasks[0].id, second.id)

    def test_invalid_updates_leave_state_unchanged(self):
        with TemporaryDirectory() as directory:
            store = ForkJobStore(Path(directory))
            job = store.create(parent_session_id="parent", tool_call_id="call_a", tasks=[
                ForkTaskInput(name="code", instruction="分析代码"),
            ])
            task = job.tasks[0]
            with self.assertRaises(ValueError):
                store.transition(job.id, task.id, status="completed", summary="跳过执行")
            store.transition(job.id, task.id, status="running")
            with self.assertRaises(ValueError):
                store.transition(job.id, task.id, status="completed", summary=" ")
            self.assertEqual(store.get(job.id).tasks[0].status, "running")
            with self.assertRaises(ValueError):
                store.get("../outside")
            with self.assertRaises(ValueError):
                store.transition(job.id, "missing", status="running")


if __name__ == "__main__":
    unittest.main()
