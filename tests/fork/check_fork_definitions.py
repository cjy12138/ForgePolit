import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.fork import check_fork_factory
from forgepilot.fork.definitions import AgentDefinitionStore
from forgepilot.fork.models import ForkTaskInput, ForkTaskState
from forgepilot.skills.models import SkillDocument, SkillMetadata
from forgepilot.skills.store import SkillStore


class ForkDefinitionTests(unittest.TestCase):
    def setup_factory(self, root, definition):
        factory, session, snapshot, _ = check_fork_factory.ForkFactoryTests().setup_factory(root)
        agents = root / "agents"
        agents.mkdir()
        (agents / "reviewer.md").write_text(definition, encoding="utf-8")
        factory.agent_store = AgentDefinitionStore(agents)
        factory.skill_store = SkillStore(root / "skills")
        return factory, session, snapshot, agents

    def test_definition_and_preloaded_skills_saved_for_resume(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, agents = self.setup_factory(root, (
                "---\nname: reviewer\ndescription: 测试分析\n"
                "tools: read_file, list_files\ndisallowedTools: [fork_tasks]\n"
                "skills: [testing]\n---\n你是测试分析助手。\n"
            ))
            for name in ("testing", "conventions"):
                factory.skill_store.save(SkillDocument(metadata=SkillMetadata(
                    name=name, description="指南", tools=["read_file"],
                ), content=f"{name} 的操作要求"))
            task = ForkTaskState(task=ForkTaskInput(
                name="review", instruction="分析测试", agent_type="reviewer", skills=["conventions"],
            ))
            first = factory(session, snapshot, task)
            self.assertEqual(len(first.skill_messages), 3)
            self.assertEqual(first.skill_messages[0]["content"], "你是测试分析助手。")
            (agents / "reviewer.md").write_text("changed", encoding="utf-8")
            second = factory(session, snapshot, task)
            self.assertEqual(second.skill_messages, first.skill_messages)
            self.assertTrue((session.path.parent / "agent-definition.json").exists())

    def test_denied_tools_cannot_be_added_by_preloaded_skill(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, _ = self.setup_factory(root, (
                "---\nname: reviewer\ndescription: 测试分析\n"
                "disallowedTools: [list_files]\nskills: [testing]\n---\n分析测试\n"
            ))
            factory.skill_store.save(SkillDocument(metadata=SkillMetadata(
                name="testing", description="目录探索", tools=["list_files"],
            ), content="查看目录"))
            task = ForkTaskState(task=ForkTaskInput(name="review", instruction="分析", agent_type="reviewer"))
            with self.assertRaises(ValueError):
                factory(session, snapshot, task)

    def test_omitted_tools_inherit_and_definition_paths_are_checked(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory, session, snapshot, _ = self.setup_factory(root, (
                "---\nname: reviewer\ndescription: 测试分析\n"
                "disallowedTools: [list_files]\n---\n分析测试\n"
            ))
            task = ForkTaskState(task=ForkTaskInput(name="review", instruction="分析", agent_type="reviewer"))
            agent = factory(session, snapshot, task)
            self.assertEqual([item["name"] for item in agent.tool_definitions], ["read_file"])
            with self.assertRaises(ValueError):
                factory.agent_store.read("../reviewer")


if __name__ == "__main__":
    unittest.main()
