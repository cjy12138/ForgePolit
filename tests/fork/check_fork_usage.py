import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from forgepilot.fork.model import DeepSeekForkModel
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.models.usage import normalize_usage
from forgepilot.fork.artifacts import inherit_artifacts
from forgepilot.artifacts.store import ArtifactStore


class ForkUsageTests(unittest.TestCase):
    def test_cache_usage_and_request_reservation(self):
        with TemporaryDirectory() as directory:
            messages = [{"role": "user", "content": "父任务"}]
            snapshot = ForkSnapshot.capture(request=DeepSeekModel(None).prepare_request(messages, []), messages=messages)
            def create(**request):
                return SimpleNamespace(usage={"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128,
                                              "prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 20},
                    choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(tool_calls=None, content="完成"))])
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            reserved = []
            path = Path(directory) / "usage.jsonl"
            model = DeepSeekForkModel(client=client, snapshot=snapshot, usage_path=path,
                                       before_request=lambda: reserved.append(True))
            model.generate([*messages, {"role": "user", "content": "子任务"}], [])
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["usage"]["prompt_cache_hit_tokens"], 100)
            self.assertEqual(record["usage"]["prompt_cache_miss_tokens"], 20)
            self.assertEqual(len(reserved), 1)
            self.assertIsNone(normalize_usage({"prompt_tokens": 1, "completion_tokens": 1}).prompt_cache_hit_tokens)

    def test_inherited_artifacts_are_copied_without_changing_parent(self):
        with TemporaryDirectory() as directory:
            parent = ArtifactStore(Path(directory) / "parent")
            child = ArtifactStore(Path(directory) / "child")
            ref = parent.save_text("完整工具输出")
            snapshot = ForkSnapshot.capture(request={}, messages=[{
                "role": "tool", "artifact": ref.model_dump(), "content": "片段",
            }])
            inherit_artifacts(snapshot, parent=parent, child=child)
            inherit_artifacts(snapshot, parent=parent, child=child)
            self.assertEqual(child.read_text(ref), "完整工具输出")
            self.assertEqual(parent.read_text(ref), "完整工具输出")


if __name__ == "__main__":
    unittest.main()
