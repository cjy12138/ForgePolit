"""离线验证：主题不限数量，索引最多 200 条，两个 Agent 可选择索引。"""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgepilot.memory.dream_agent import AutoDreamAgent
from forgepilot.memory.dream_models import DreamProposal
from forgepilot.memory.dream_publication import publish_dream_proposal
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.index import MAX_INDEX_BYTES, MemoryIndexChange
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.service import MemoryExtractionService
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tools.memory import glob_memory, grep_memory
from forgepilot.contracts import ToolContext


def document(index, description="可复用的项目约定"):
    return MemoryDocument(
        filename=f"topic_{index:03}.md",
        metadata=MemoryMetadata(
            name=f"主题 {index}", type="project", description=description,
            source_session_id="source-session", source_entry_ids=["source-entry"],
        ),
        content=f"主题 {index} 的长期记忆，检索标记 unique_{index}。",
    )


class SelectionModel:
    max_output_tokens = 2048

    def __init__(self, dream=False, new_topic=False):
        self.dream = dream
        self.new_topic = new_topic
        self.calls = 0

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1
        data = json.loads(messages[1]["content"])
        assert data["memory_index"]["max_index_entries"] == 200
        assert data["memory_index"]["topic_count_is_unlimited"] is True
        manifest = data["manifest" if self.dream else "existing_memory_manifest"]
        assert "topic_204.md" in manifest
        arguments = {"index_change": {"remove": ["topic_000.md"], "add": ["topic_204.md"]}}
        if self.dream:
            arguments.update(rewrites=[], deletes=[], conflicts=[])
        else:
            arguments["memories"] = []
            if self.new_topic:
                arguments["index_change"] = {}  # 新主题保存，但不要求进入索引。
                arguments["memories"] = [{
                    "filename": "new_fact.md", "name": "新约定", "type": "project",
                    "description": "新的长期约定", "content": "项目发布前检查测试。",
                    "source_entry_ids": [data["focus_user_entry_id"]],
                }]
        return {"type": "tool_call", "id": "select_index", "name": "submit_dream_proposal" if self.dream else "submit_memory_proposal", "arguments": arguments}


class MemoryIndexChecks(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = MarkdownMemoryStore(self.root / "memory")
        self.store.apply_changes([document(index) for index in range(205)])
        self.budget = ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=1000)

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.store.root.iterdir() if path.suffix in {".md", ".json"}}

    def extraction_service(self, model):
        sessions = SessionStore(self.root / "session.jsonl")
        sessions.create(SessionHeader(owner_id="user", project_id="project", cwd=str(self.root)))
        sessions.append_message({"role": "user", "content": "请记住发布前检查测试。"})
        sessions.append_message({"role": "assistant", "type": "final", "content": "好的。"})
        return MemoryExtractionService(
            sessions=sessions, memory_store=self.store,
            agent=MemoryExtractionAgent(model=model, memory_store=self.store, context_budget=self.budget),
        )

    def test_more_than_200_topics_remain_searchable(self):
        self.assertEqual(len(self.store.list_filenames()), 205)
        self.assertEqual(len(self.store.read_index().splitlines()), 200)
        self.assertNotIn("topic_204.md", self.store.read_index())
        candidates, skipped = self.store.scan_headers()
        self.assertEqual(len(candidates), 205)
        self.assertEqual(skipped, [])
        context = ToolContext(workspace=self.store.root)
        self.assertIn("topic_204.md", json.loads(glob_memory(pattern="topic_204.md", memory_store=self.store, context=context))["files"])
        self.assertIn("unique_204", grep_memory(keyword="unique_204", memory_store=self.store, context=context))

    def test_extraction_agent_can_change_only_index(self):
        model = SelectionModel()
        originals = {name: self.store.read_text(name) for name in self.store.list_filenames()}
        result = self.extraction_service(model).process_completed_turn()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["filenames"], [])
        self.assertEqual(model.calls, 1)
        self.assertIn("topic_204.md", self.store.read_index())
        self.assertNotIn("topic_000.md", self.store.read_index())
        for name, text in originals.items():
            self.assertEqual(self.store.read_text(name), text)
        self.store.save(self.store.read("topic_000.md"))
        reopened = MarkdownMemoryStore(self.store.root)
        self.assertNotIn("topic_000.md", reopened.read_index())
        self.assertIn("topic_204.md", reopened.read_index())

    def test_extraction_can_save_unindexed_topic_when_full(self):
        result = self.extraction_service(SelectionModel(new_topic=True)).process_completed_turn()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["unindexed_filenames"], ["new_fact.md"])
        self.assertIn("new_fact.md", self.store.list_filenames())
        self.assertNotIn("new_fact.md", self.store.read_index())
        self.assertEqual(len(self.store.read_index().splitlines()), 200)

    def test_dream_agent_index_only_publication_and_backup(self):
        model = SelectionModel(dream=True)
        agent = AutoDreamAgent(model=model, memory_store=self.store, context_budget=self.budget)
        before = self.snapshot()
        proposal = agent.prepare()
        self.assertEqual(before, self.snapshot())
        result = publish_dream_proposal(proposal, memory_store=self.store, read_versions=agent.read_versions, fully_read=agent.fully_read)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["rewritten"], [])
        self.assertIn("topic_204.md", self.store.read_index())
        backup = Path(result["backup_path"])
        for name in ["MEMORY.md", ".memory-index.json"]:
            self.assertEqual((backup / name).read_bytes(), before[name])
        self.assertEqual(len(self.store.list_filenames()), 205)

    def test_invalid_selection_does_not_write_new_topic(self):
        before = self.snapshot()
        for change in [MemoryIndexChange(add=["missing.md"]), MemoryIndexChange(add=["topic_204.md"])]:
            with self.assertRaises(ValueError):
                self.store.apply_changes([document(206)], index_change=change)
            self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            MemoryIndexChange(add=["topic_204.md"], remove=["topic_204.md"])

    def test_byte_limit_applies_to_index_only(self):
        other = MarkdownMemoryStore(self.root / "long-descriptions")
        docs = [document(index, "很长的描述" * 25) for index in range(205)]
        other.apply_changes(docs, index_change=MemoryIndexChange())
        self.assertEqual(len(other.list_filenames()), 205)
        with self.assertRaisesRegex(ValueError, "25 KiB"):
            other.apply_changes([], index_change=MemoryIndexChange(add=[item.filename for item in docs[:200]]))
        self.assertEqual(other.read_index(), "")
        other.apply_changes([], index_change=MemoryIndexChange(add=["topic_204.md"]))
        self.assertLessEqual(len(other.read_index().encode("utf-8")), MAX_INDEX_BYTES)

    def test_legacy_index_migration_keeps_subset(self):
        self.store.apply_changes([], index_change=MemoryIndexChange(remove=[f"topic_{index:03}.md" for index in range(1, 200)]))
        (self.store.root / ".memory-index.json").unlink()
        reopened = MarkdownMemoryStore(self.store.root)
        self.assertEqual(reopened.get_index_context()["indexed_filenames"], ["topic_000.md"])
        self.assertEqual(len(reopened.read_index().splitlines()), 1)
        self.assertTrue((reopened.root / ".memory-index.json").exists())
        self.assertEqual(len(reopened.list_filenames()), 205)

    def test_write_failure_rolls_back_selection_and_body(self):
        before = self.snapshot()
        real_replace = os.replace

        def fail_state(source, target):
            if Path(target) == self.store.root / ".memory-index.json":
                raise OSError("模拟索引状态写入失败")
            return real_replace(source, target)

        with patch("forgepilot.memory.store.os.replace", side_effect=fail_state):
            with self.assertRaises(OSError):
                self.store.apply_changes([document(206)], index_change=MemoryIndexChange(remove=["topic_000.md"], add=["topic_204.md"]))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
