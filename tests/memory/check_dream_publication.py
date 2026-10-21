from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.dream_models import DreamProposal
from forgepilot.memory.dream_publication import publish_dream_proposal
from forgepilot.memory.dream_validation import document_sha256
from forgepilot.memory.models import (
    MemoryDocument,
    MemoryMetadata,
    MemorySource,
)
from forgepilot.memory.store import MarkdownMemoryStore


def main():
    with TemporaryDirectory() as directory:
        store = MarkdownMemoryStore(Path(directory))

        for filename, session_id, content in [
            (
                "testing_preferences.md",
                "session_a",
                "优先使用 unittest。",
            ),
            (
                "verification_preferences.md",
                "session_b",
                "按实际结果报告测试。",
            ),
        ]:
            store.save(MemoryDocument(
                filename=filename,
                metadata=MemoryMetadata(
                    name="测试偏好",
                    type="feedback",
                    description=content,
                    source_session_id=session_id,
                    source_entry_ids=["entry_1"],
                ),
                content=content,
            ))

        versions = {
            filename: document_sha256(store.read(filename))
            for filename in store.list_filenames()
        }

        proposal = DreamProposal.model_validate({
            "rewrites": [{
                "filename": "testing_preferences.md",
                "name": "测试偏好",
                "type": "feedback",
                "description": "测试框架与报告要求。",
                "content": "优先使用 unittest，按实际结果报告测试。",
                "source_files": [
                    "testing_preferences.md",
                    "verification_preferences.md",
                ],
            }],
            "deletes": [{
                "filename": "verification_preferences.md",
                "reason": "内容已合并到测试偏好主题。",
                "replacement": "testing_preferences.md",
                "source_files": [
                    "testing_preferences.md",
                    "verification_preferences.md",
                ],
            }],
            "conflicts": [],
        })

        def markdown_snapshot():
            return {
                path.name: path.read_bytes()
                for path in store.root.glob("*.md")
            }

        before = markdown_snapshot()

        # 模拟持有过期版本，必须在修改文件前拒绝。
        stale_versions = dict(versions)
        stale_versions["testing_preferences.md"] = "old-hash"

        try:
            publish_dream_proposal(
                proposal,
                memory_store=store,
                read_versions=stale_versions,
                fully_read=set(versions),
            )
        except ValueError as exc:
            assert "来源主题已变化" in str(exc)
        else:
            raise AssertionError("过期版本应拒绝发布")

        assert markdown_snapshot() == before
        assert not (store.root / ".dream-backups").exists()
        print("过期候选拒绝发布，正式文件不变：通过")

        result = publish_dream_proposal(
            proposal,
            memory_store=store,
            read_versions=versions,
            fully_read=set(versions),
        )

        assert result["status"] == "published"
        assert result["rewritten"] == ["testing_preferences.md"]
        assert result["deleted"] == ["verification_preferences.md"]

        assert store.list_filenames() == ["testing_preferences.md"]

        merged = store.read("testing_preferences.md")
        assert merged.content == proposal.rewrites[0].content
        assert merged.metadata.source_session_id == "session_a"
        assert merged.metadata.previous_sources == [
            MemorySource(
                source_session_id="session_b",
                source_entry_ids=["entry_1"],
            )
        ]
        print("合并正文、保留来源、删除重复主题：通过")

        # 比较原始字节，确认备份没有重新生成旧正文。
        backup = Path(result["backup_path"])

        for name, content in before.items():
            assert (backup / name).read_bytes() == content

        backed_proposal = DreamProposal.model_validate_json(
            (backup / "proposal.json").read_text(
                encoding="utf-8"
            )
        )
        assert backed_proposal == proposal
        print("旧主题、旧索引与整理候选均已备份：通过")

        # 直接读已发布的索引，避免 read_index() 重建后掩盖问题。
        index = (store.root / "MEMORY.md").read_text(
            encoding="utf-8"
        )

        assert "(testing_preferences.md)" in index
        assert "(verification_preferences.md)" not in index
        assert "测试框架与报告要求。" in index
        assert len(index.splitlines()) <= 200
        print("已发布索引与最终主题一致：通过")


if __name__ == "__main__":
    main()