"""旧记忆 Markdown 版本工具与两个记忆 Agent 的离线检查。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from forgepilot.contracts import ToolContext
from forgepilot.memory.dream_agent import AutoDreamAgent
from forgepilot.memory.dream_validation import document_sha256
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.memory.versions import MarkdownMemoryVersions
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.memory_versions import build_memory_version_tools


class HistoricalModel:
    max_output_tokens = 1024

    def __init__(self, dream=False):
        self.calls = 0
        self.dream = dream
        self.agent = None
        self.old_text = None

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1
        if self.calls == 1:
            self.assert_tools = {tool["name"] for tool in tools}
            assert {"list_memory_versions", "read_memory_version"}.issubset(self.assert_tools)
            name, arguments = "list_memory_versions", {"filename": "project_rules.md"}
        elif self.calls == 2:
            result = json.loads(messages[-1]["content"])
            self.version_id = result["versions"][0]["version_id"]
            name, arguments = "read_memory_version", {"filename": "project_rules.md", "version_id": self.version_id, "limit": 8000}
        elif self.calls == 3:
            result = json.loads(messages[-1]["content"])
            assert result["historical"] is True
            assert "旧版约定" in result["content"]
            self.old_text = result["content"]
            assert self.agent.read_versions == {}  # 历史读取不能满足当前文件的版本检查。
            if self.dream:
                assert self.agent.fully_read == set()
            name, arguments = "read_memory", {"filename": "project_rules.md"}
        elif self.calls == 4:
            result = json.loads(messages[-1]["content"])
            assert "当前约定" in result["content"]
            if self.dream:
                name, arguments = "submit_dream_proposal", {"rewrites": [], "deletes": [], "conflicts": [], "index_change": {}}
            else:
                name, arguments = "submit_memory_proposal", {"memories": [], "index_change": {}}
        else:
            raise AssertionError("不应继续调用模型")
        return {"type": "tool_call", "id": f"call_{self.calls}", "name": name, "arguments": arguments}


class MemoryVersionChecks(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = MarkdownMemoryStore(self.root / "memory")
        self.store.save(MemoryDocument(
            filename="project_rules.md",
            metadata=MemoryMetadata(name="项目约定", type="project", description="旧规则描述", source_session_id="old-session", source_entry_ids=["old-entry"]),
            content="旧版约定：以前采用旧流程。\n" * 12,
        ))
        self.old_bytes = (self.store.root / "project_rules.md").read_bytes()
        updated = self.store.read("project_rules.md")
        updated.content = "当前约定：采用新流程。"
        updated.metadata.description = "当前规则描述"
        result = self.store.apply_changes([updated], backup=True, backup_kind="extract", proposal_json="{}")
        self.backup = Path(result["backup_path"])
        self.version_id = f"extract/{self.backup.name}"
        self.versions = MarkdownMemoryVersions(self.store)
        self.context = ToolContext(workspace=self.store.root)
        self.registry = ToolRegistry(build_memory_version_tools(memory_store=self.store))

    def tree(self):
        return {str(path.relative_to(self.store.root)): path.read_bytes() for path in self.store.root.rglob("*") if path.is_file() and path.name != ".memory.lock"}

    def test_list_and_read_raw_markdown_in_chunks_without_writing(self):
        before = self.tree()
        result = self.registry.execute("list_memory_versions", {"filename": "project_rules.md"}, context=self.context)
        self.assertTrue(result.ok, result.output)
        data = json.loads(result.output)
        self.assertEqual(data["total_matches"], 1)
        self.assertEqual(data["versions"][0]["version_id"], self.version_id)
        self.assertEqual(data["versions"][0]["backup_status"], "published")
        offset, pieces = 0, []
        while True:
            result = self.registry.execute("read_memory_version", {"filename": "project_rules.md", "version_id": self.version_id, "offset": offset, "limit": 45}, context=self.context)
            self.assertTrue(result.ok, result.output)
            data = json.loads(result.output)
            pieces.append(data["content"])
            if data["next_offset"] is None:
                break
            offset = data["next_offset"]
        self.assertEqual("".join(pieces).encode("utf-8"), self.old_bytes)
        self.assertEqual(self.tree(), before)

    def test_dream_deleted_topic_remains_readable(self):
        self.store.save(MemoryDocument(filename="duplicate.md", metadata=self.store.read("project_rules.md").metadata.model_copy(deep=True), content="被合并的旧记忆正文。"))
        result = self.store.apply_changes([], deletes=["duplicate.md"], backup=True, proposal_json="{}")
        self.assertNotIn("duplicate.md", self.store.list_filenames())
        data = self.versions.list_versions("duplicate.md")
        self.assertEqual(data["versions"][0]["version_id"], f"dream/{Path(result['backup_path']).name}")
        old = self.versions.read_version("duplicate.md", data["versions"][0]["version_id"])
        self.assertIn("被合并的旧记忆正文", old["content"])

    def test_unfinished_backup_is_hidden_and_not_readable(self):
        old_dirs = set((self.store.root / ".extract-backups").iterdir())
        real_write = Path.write_bytes

        def fail_backup(path, content):
            if ".extract-backups" in path.parts:
                raise OSError("备份写入失败")
            return real_write(path, content)

        with patch.object(Path, "write_bytes", new=fail_backup):
            with self.assertRaises(OSError):
                self.store.apply_changes([self.store.read("project_rules.md")], backup=True, backup_kind="extract")
        failed, = set((self.store.root / ".extract-backups").iterdir()) - old_dirs
        self.assertEqual(self.versions.list_versions("project_rules.md")["total_matches"], 1)
        with self.assertRaisesRegex(ValueError, "尚未完成发布"):
            self.versions.read_version("project_rules.md", f"extract/{failed.name}")

    def test_legacy_backup_is_readable_with_unknown_status(self):
        legacy = self.store.root / ".extract-backups" / uuid4().hex
        legacy.mkdir()
        (legacy / "project_rules.md").write_bytes(self.old_bytes)
        data = self.versions.read_version("project_rules.md", f"extract/{legacy.name}", limit=8000)
        self.assertEqual(data["backup_status"], "legacy_unknown")
        self.assertEqual(data["content"].encode("utf-8"), self.old_bytes)
        first_page = self.versions.list_versions("project_rules.md", limit=1)
        second_page = self.versions.list_versions("project_rules.md", offset=1, limit=1)
        self.assertEqual(first_page["next_offset"], 1)
        self.assertNotEqual(first_page["versions"][0]["version_id"], second_page["versions"][0]["version_id"])

    def test_corrupt_old_markdown_is_rejected(self):
        (self.backup / "project_rules.md").write_bytes(self.old_bytes + b"changed")
        with self.assertRaisesRegex(ValueError, "校验信息不一致"):
            self.versions.read_version("project_rules.md", self.version_id)
        data = self.versions.list_versions("project_rules.md")
        self.assertEqual(data["versions"], [])
        self.assertEqual(data["skipped"], 1)

    def test_scoped_paths_inputs_and_offsets(self):
        for arguments in [
            {"filename": "../project_rules.md", "version_id": self.version_id},
            {"filename": "project_rules.md", "version_id": "../other"},
            {"filename": "project_rules.md", "version_id": self.version_id, "path": "anything"},
            {"filename": "project_rules.md", "version_id": self.version_id, "offset": 999999},
        ]:
            self.assertFalse(self.registry.execute("read_memory_version", arguments, context=self.context).ok)
        other = MarkdownMemoryStore(self.root / "other-scope")
        other.save(self.store.read("project_rules.md"))
        result = other.apply_changes([other.read("project_rules.md")], backup=True, backup_kind="extract")
        other_id = f"extract/{Path(result['backup_path']).name}"
        with self.assertRaisesRegex(ValueError, "版本不存在"):
            self.versions.read_version("project_rules.md", other_id)

    def test_shared_read_quota_and_resume_offset(self):
        consumed = []

        def consume(text):
            consumed.append(text)
            return text[:7]

        registry = ToolRegistry(build_memory_version_tools(memory_store=self.store, consume_read=consume))
        result = registry.execute("read_memory_version", {"filename": "project_rules.md", "version_id": self.version_id}, context=self.context)
        self.assertTrue(result.ok, result.output)
        data = json.loads(result.output)
        self.assertEqual(len(consumed), 1)
        self.assertEqual(data["content"], self.old_bytes.decode("utf-8")[:7])
        self.assertEqual(data["next_offset"], 7)

    def test_both_agents_can_compare_history_without_claiming_current_read(self):
        sessions = SessionStore(self.root / "session.jsonl")
        sessions.create(SessionHeader(owner_id="user", project_id="project", cwd=str(self.root)))
        sessions.append_message({"role": "user", "content": "检查已有记忆的变化。"})
        sessions.append_message({"role": "assistant", "type": "final", "content": "好的。"})
        snapshot = build_memory_snapshot(sessions)
        original_session = sessions.path.read_bytes()
        before = self.tree()
        budget = ContextBudget(context_window=16000, max_output_tokens=1024, safety_margin=1000)
        for dream in [False, True]:
            model = HistoricalModel(dream=dream)
            cls = AutoDreamAgent if dream else MemoryExtractionAgent
            agent = cls(model=model, memory_store=self.store, context_budget=budget)
            model.agent = agent
            if dream:
                agent.prepare()
            else:
                agent.extract(snapshot)
            self.assertEqual(model.calls, 4)
            self.assertEqual(model.old_text.encode("utf-8"), self.old_bytes)
            self.assertEqual(agent.read_versions["project_rules.md"], document_sha256(self.store.read("project_rules.md")))
        self.assertEqual(self.tree(), before)
        self.assertEqual(sessions.path.read_bytes(), original_session)


if __name__ == "__main__":
    unittest.main(verbosity=2)
