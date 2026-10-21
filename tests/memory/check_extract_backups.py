"""验证 extract 更新成功后，旧版 Markdown 仍持久存在。"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgepilot.memory.dream_validation import document_sha256
from forgepilot.memory.models import MemoryCandidate, MemoryDocument, MemoryMetadata, MemoryProposal
from forgepilot.memory.publication import publish_memories, publish_memory_updates
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


class ExtractBackupChecks(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.sessions = SessionStore(root / "session.jsonl")
        self.sessions.create(SessionHeader(owner_id="user", project_id="demo", cwd=str(root)))
        self.sessions.append_message({"role": "user", "content": "更新项目长期约定。"})
        self.sessions.append_message({"role": "assistant", "type": "final", "content": "好的。"})
        self.snapshot = build_memory_snapshot(self.sessions)
        self.store = MarkdownMemoryStore(root / "memory")
        self.store.save(MemoryDocument(
            filename="project_rules.md",
            metadata=MemoryMetadata(name="项目约定", type="project", description="旧项目约定", source_session_id="old-session", source_entry_ids=["old-entry"]),
            content="旧版记忆正文。",
        ))
        self.backup_root = self.store.root / ".extract-backups"

    def candidate(self, content, filename="project_rules.md"):
        return MemoryCandidate(filename=filename, name="项目约定", type="project", description="更新后的项目约定", content=content, source_entry_ids=[self.snapshot.user_entry_id])

    def files(self):
        return {path.name: path.read_bytes() for path in self.store.root.iterdir() if path.suffix in {".md", ".json"}}

    def publish(self, proposal, publisher=publish_memories, versions=None):
        return publisher(
            proposal, snapshot=self.snapshot, sessions=self.sessions, memory_store=self.store,
            read_versions=versions if versions is not None else {"project_rules.md": document_sha256(self.store.read("project_rules.md"))},
        )

    def test_update_only_entrypoint_preserves_exact_old_files(self):
        before = self.files()
        proposal = MemoryProposal(memories=[self.candidate("新版本正文。")])
        self.publish(proposal, publisher=publish_memory_updates)
        backup, = self.backup_root.iterdir()
        for name, content in before.items():
            self.assertEqual((backup / name).read_bytes(), content)
        self.assertEqual(MemoryProposal.model_validate_json((backup / "proposal.json").read_text(encoding="utf-8")), proposal)
        self.assertEqual(self.store.read("project_rules.md").content, "新版本正文。")

    def test_mixed_and_repeated_updates_keep_separate_versions(self):
        first_old = (self.store.root / "project_rules.md").read_bytes()
        self.publish(MemoryProposal(memories=[self.candidate("第一版更新。"), self.candidate("新增参考。", "new_reference.md")]))
        first_backup, = self.backup_root.iterdir()
        self.assertEqual((first_backup / "project_rules.md").read_bytes(), first_old)
        self.assertFalse((first_backup / "new_reference.md").exists())
        second_old = (self.store.root / "project_rules.md").read_bytes()
        self.publish(MemoryProposal(memories=[self.candidate("第二版更新。")]))
        second_backup, = set(self.backup_root.iterdir()) - {first_backup}
        self.assertEqual((second_backup / "project_rules.md").read_bytes(), second_old)
        self.assertEqual((first_backup / "project_rules.md").read_bytes(), first_old)
        reopened = MarkdownMemoryStore(self.store.root)
        self.assertEqual(reopened.read("project_rules.md").content, "第二版更新。")
        self.assertIn("旧版记忆正文。", (first_backup / "project_rules.md").read_text(encoding="utf-8"))
        self.assertEqual(reopened.list_filenames(), ["new_reference.md", "project_rules.md"])
        self.assertEqual(len(reopened.scan_headers()[0]), 2)

    def test_invalid_version_never_creates_backup_or_changes_files(self):
        before = self.files()
        with self.assertRaisesRegex(ValueError, "主题已变化"):
            self.publish(MemoryProposal(memories=[self.candidate("不应保存。")]), versions={"project_rules.md": "stale"})
        self.assertEqual(self.files(), before)
        self.assertFalse(self.backup_root.exists())

    def test_backup_failure_does_not_overwrite_current_memory(self):
        before = self.files()
        real_write = Path.write_bytes

        def fail_backup(path, content):
            if self.backup_root in path.parents:
                raise OSError("模拟备份失败")
            return real_write(path, content)

        with patch.object(Path, "write_bytes", new=fail_backup):
            with self.assertRaisesRegex(OSError, "模拟备份失败"):
                self.publish(MemoryProposal(memories=[self.candidate("不应保存。")]))
        self.assertEqual(self.files(), before)

    def test_new_topic_only_does_not_create_old_version_backup(self):
        self.publish(MemoryProposal(memories=[self.candidate("新主题正文。", "new_topic.md")]))
        self.assertFalse(self.backup_root.exists())
        self.assertEqual(self.store.read("new_topic.md").content, "新主题正文。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
