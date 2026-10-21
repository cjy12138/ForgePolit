import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.fork.models import ForkTaskInput
from forgepilot.fork.store import ForkJobStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.validation import pending_tool_calls


class ForkSessionTests(unittest.TestCase):
    def test_messages_are_isolated_and_pending_calls_survive_reopen(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ForkJobStore(root)
            header = SessionHeader(project_id="demo", owner_id="user", cwd=str(root))
            job = store.create(parent_session_id=header.session_id, tool_call_id="fork_a", tasks=[
                ForkTaskInput(name="code", instruction="分析代码"),
            ])
            other = store.create(parent_session_id=header.session_id, tool_call_id="fork_b", tasks=[
                ForkTaskInput(name="tests", instruction="查找测试"),
            ])
            messages = [{"role": "user", "content": "父任务"}]
            first, second = job.tasks[0], other.tasks[0]
            a = store.initialize_session(job.id, first.id, parent_header=header, messages=messages)
            b = store.initialize_session(other.id, second.id, parent_header=header, messages=messages)
            a.append_message({"role": "assistant", "type": "tool_call", "id": "read_a",
                              "name": "read_file", "arguments": {"path": "README.md"}})
            reopened = ForkJobStore(root)
            restored = reopened.get_session(job.id, first.id)
            self.assertEqual(pending_tool_calls(restored.get_current_branch()), {"read_a"})
            restored.append_message({"role": "tool", "tool_call_id": "read_a",
                                     "content": "项目说明", "is_error": False, "exit_code": None})
            restored.append_message({"role": "assistant", "type": "final", "content": "已完成探索"})
            self.assertEqual(pending_tool_calls(restored.get_current_branch()), set())
            self.assertEqual(len(b.get_current_branch()), 2)
            self.assertEqual(messages, [{"role": "user", "content": "父任务"}])
            self.assertEqual(reopened.get(job.id).tasks[0].session_id, first.id)
            with self.assertRaises(ValueError):
                reopened.initialize_session(job.id, first.id, parent_header=header, messages=messages)

    def test_incomplete_parent_history_is_not_published(self):
        with TemporaryDirectory() as directory:
            store = ForkJobStore(Path(directory))
            header = SessionHeader(project_id="demo", owner_id="user", cwd=directory)
            job = store.create(parent_session_id=header.session_id, tool_call_id="fork_a", tasks=[
                ForkTaskInput(name="code", instruction="分析代码"),
            ])
            task = job.tasks[0]
            with self.assertRaises(ValueError):
                store.initialize_session(job.id, task.id, parent_header=header, messages=[
                    {"role": "assistant", "type": "tool_call", "id": "pending",
                     "name": "read_file", "arguments": {"path": "README.md"}},
                ])
            self.assertIsNone(store.get(job.id).tasks[0].session_id)
            self.assertFalse((Path(directory) / job.id / "tasks" / task.id / "session.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
