from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.dream_models import DreamProposal
from forgepilot.memory.dream_publication import prepare_dream_documents
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

        shared_source = MemorySource(
            source_session_id="session_shared",
            source_entry_ids=["entry_shared"],
        )

        for filename, session_id, entry_id, content in [
            (
                "testing_preferences.md",
                "session_a",
                "entry_a",
                "优先使用 unittest。",
            ),
            (
                "verification_preferences.md",
                "session_b",
                "entry_b",
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
                    source_entry_ids=[entry_id],
                    previous_sources=[
                        shared_source.model_copy(deep=True)
                    ],
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
                # 故意把另一个主题放在前面。
                "source_files": [
                    "verification_preferences.md",
                    "testing_preferences.md",
                ],
            }],
            "deletes": [],
            "conflicts": [],
        })

        before = {
            path.name: path.read_bytes()
            for path in store.root.glob("*.md")
        }

        documents = prepare_dream_documents(
            proposal,
            memory_store=store,
            read_versions=versions,
            fully_read=set(versions),
        )

        assert len(documents) == 1
        merged = documents[0]

        assert merged.metadata.source_session_id == "session_a"
        assert merged.metadata.source_entry_ids == ["entry_a"]
        print("更新主题时保留原主题主要来源：通过")

        assert merged.metadata.previous_sources == [
            shared_source,
            MemorySource(
                source_session_id="session_b",
                source_entry_ids=["entry_b"],
            ),
        ]
        print("继承其他主题及历史来源，共同来源不重复：通过")

        assert merged.content == proposal.rewrites[0].content

        after = {
            path.name: path.read_bytes()
            for path in store.root.glob("*.md")
        }

        assert before == after
        print("构建整理文档不修改主题和索引：通过")


if __name__ == "__main__":
    main()