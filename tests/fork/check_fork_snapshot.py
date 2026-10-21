import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from forgepilot.fork.models import ForkTaskInput
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.fork.store import ForkJobStore
from forgepilot.models.deepseek import DeepSeekModel


class ForkSnapshotTests(unittest.TestCase):
    def test_capture_survives_mutation_and_reopen(self):
        messages = [{"role": "user", "content": "分析代码"}]
        request = {"model": "demo", "messages": messages, "tools": []}
        snapshot = ForkSnapshot.capture(request=request, messages=messages)
        messages[0]["content"] = "新任务"
        snapshot.request()["tools"].append({"name": "changed"})
        self.assertEqual(snapshot.messages()[0]["content"], "分析代码")
        self.assertEqual(snapshot.request()["tools"], [])
        with TemporaryDirectory() as directory:
            store = ForkJobStore(Path(directory))
            job = store.create(parent_session_id="parent", tool_call_id="call", tasks=[
                ForkTaskInput(name="explore", instruction="探索"),
            ])
            store.save_snapshot(job.id, snapshot)
            self.assertEqual(ForkJobStore(Path(directory)).get_snapshot(job.id), snapshot)
            with self.assertRaises(ValueError):
                store.save_snapshot(job.id, snapshot)
            corrupted = snapshot.model_dump()
            corrupted["messages_json"] = "[]"
            with self.assertRaises(ValueError):
                ForkSnapshot.model_validate(corrupted)

    def test_model_captures_actual_request_but_not_triggering_response(self):
        sent = []

        def create(**request):
            sent.append(request)
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(tool_calls=None, content="完成"),
            )])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        model = DeepSeekModel(client)
        messages = [{"role": "user", "content": "父任务"}]
        model.generate(messages, [])
        self.assertEqual(model.last_fork_snapshot.request(), sent[0])
        self.assertEqual(model.last_fork_snapshot.messages(), messages)
        messages.append({"role": "assistant", "type": "final", "content": "完成"})
        self.assertEqual(len(model.last_fork_snapshot.messages()), 1)


if __name__ == "__main__":
    unittest.main()
