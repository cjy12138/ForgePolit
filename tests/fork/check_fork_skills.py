import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.fork import check_fork_factory
from forgepilot.fork.models import ForkTaskInput, ForkTaskState
from forgepilot.skills.models import SkillDocument, SkillMetadata
from forgepilot.skills.store import SkillStore


class SelectingSkill:
    def __init__(self, name):
        self.name = name
        self.tasks = []

    def select(self, *, task, candidates):
        self.tasks.append(task)
        return self.name


class ForkSkillTests(unittest.TestCase):
    def test_selection_loads_tools_and_reopen_uses_saved_instructions(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, _ = check_fork_factory.ForkFactoryTests().setup_factory(root)
            store = SkillStore(root / "skills")
            store.save(SkillDocument(metadata=SkillMetadata(
                name="explore", description="探索测试", tools=["list_files"],
            ), content="先查看目录再分析测试。"))
            selector = SelectingSkill("explore")
            factory.skill_store, factory.skill_selector = store, selector
            task = ForkTaskState(task=ForkTaskInput(
                name="tests", instruction="查找相关测试", base_tools=["read_file"],
            ))
            before = session.path.read_bytes()
            first = factory(session, snapshot, task)
            self.assertEqual(selector.tasks, ["查找相关测试"])
            self.assertEqual({tool["name"] for tool in first.tool_definitions}, {"read_file", "list_files"})
            self.assertIn("先查看目录", first.context_controller.prepare().messages[0]["content"])
            store.save(SkillDocument(metadata=SkillMetadata(name="explore", description="变化"),
                                     content="新版本指南"), replace=True)
            second = factory(session, snapshot, task)
            self.assertEqual(len(selector.tasks), 1)
            self.assertEqual(second.skill_messages, first.skill_messages)
            self.assertEqual(session.path.read_bytes(), before)

    def test_no_skill_keeps_only_base_tools(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, _ = check_fork_factory.ForkFactoryTests().setup_factory(root)
            factory.skill_store = SkillStore(root / "skills")
            factory.skill_selector = SelectingSkill(None)
            task = ForkTaskState(task=ForkTaskInput(
                name="tests", instruction="查找测试", base_tools=["read_file"],
            ))
            agent = factory(session, snapshot, task)
            self.assertEqual([tool["name"] for tool in agent.tool_definitions], ["read_file"])
            self.assertEqual(agent.skill_messages, [])


if __name__ == "__main__":
    unittest.main()
