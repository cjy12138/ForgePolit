import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.fork import check_fork_factory
from tests.context.check_session_compactor import FakeSummaryModel
from forgepilot.fork.models import ForkTaskInput, ForkTaskState
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.store import SessionStore
from forgepilot.tools.artifacts import build_artifact_tools
from forgepilot.artifacts.store import ArtifactStore


class OfflineSummaryModel(DeepSeekModel):
    def __init__(self):
        super().__init__(None, max_output_tokens=4096)
        self.fake = FakeSummaryModel()

    def generate(self, messages, tools):
        return self.fake.generate(messages, tools)


class ForkContextTests(unittest.TestCase):
    def test_compaction_only_updates_child_session_and_keeps_skill_prefix(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, child, snapshot, _ = check_fork_factory.ForkFactoryTests().setup_factory(root)
            parent_copy = root / "parent.jsonl"
            parent_copy.write_bytes(child.path.read_bytes())
            before = parent_copy.read_bytes()
            child.append_message({"role": "assistant", "type": "final", "content": "历史回答"})
            child.append_message({"role": "user", "content": "历史信息" * 1000})
            child.append_message({"role": "assistant", "type": "final", "content": "已分析"})
            child.append_message({"role": "user", "content": "继续任务"})
            task = ForkTaskState(task=ForkTaskInput(name="explore", instruction="继续任务"))
            agent = factory(child, snapshot, task)
            controller = agent.context_controller
            controller.compactor.summarizer.summarizer.model = OfflineSummaryModel()
            controller.compactor.plan_builder = lambda branch: plan_compaction(branch, keep_recent_groups=1)
            controller.compactor.publication_check = None
            record = controller.compactor.compact(keep_recent_groups=1)
            self.assertIsNotNone(record)
            self.assertEqual(parent_copy.read_bytes(), before)
            self.assertEqual(snapshot.messages(), [{"role": "user", "content": "父任务"}])
            reopened = SessionStore(child.path)
            self.assertTrue(any(isinstance(item, CompactionEntry) for item in reopened.get_current_branch()))
            messages = agent.build_context_view(reopened.get_current_branch())
            self.assertEqual(messages[-1]["content"], "继续任务")
            self.assertEqual(controller.compactor.store.path, child.path)
            self.assertEqual(controller.collapser.store.path, child.path)
            self.assertEqual(agent.artifact_store.root, (child.path.parent / "artifacts").resolve())

    def test_snip_reader_is_rebound_to_child_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, tools = check_fork_factory.ForkFactoryTests().setup_factory(root)
            parent_artifacts = ArtifactStore(root / "parent-artifacts")
            # 此处补一个父读取器，工厂必须替换其绑定。
            factory.parent_tools = tools._copy_permission_config(tools._new_registry({
                **tools._tools,
                **build_artifact_tools(session_store=session, artifact_store=parent_artifacts),
            }))
            task = ForkTaskState(task=ForkTaskInput(
                name="explore", instruction="读取", base_tools=["read_file", "read_artifact"],
            ))
            agent = factory(session, snapshot, task)
            self.assertTrue(agent.context_controller.enable_snip)
            handler = agent.tools._tools["read_artifact"]["handler"]
            self.assertIs(handler.keywords["artifact_store"], agent.artifact_store)
            self.assertIs(handler.keywords["session_store"], session)
            self.assertNotEqual(handler.keywords["artifact_store"].root, parent_artifacts.root)


if __name__ == "__main__":
    unittest.main()
