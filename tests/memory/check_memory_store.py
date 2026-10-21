from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.models import (
    MemoryDocument,
    MemoryMetadata,
)
from forgepilot.memory.store import MarkdownMemoryStore


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        document = MemoryDocument(
            filename="feedback_testing.md",
            metadata=MemoryMetadata(
                name="测试反馈",
                type="feedback",
                description="根据实际执行结果报告测试情况。",
                source_session_id="demo-session",
                source_entry_ids=["demo-entry"],
            ),
            content="未运行测试时明确说明，不虚构验证。",
        )

        store = MarkdownMemoryStore(root)
        store.save(document)

        # 重新创建存储对象，验证数据确实来自磁盘。
        reopened = MarkdownMemoryStore(root)
        assert reopened.read(document.filename) == document

        print(
            (root / document.filename).read_text(
                encoding="utf-8"
            )
        )
        print("Markdown 保存、重新读取一致：通过")
        index = reopened.read_index()

        assert "(feedback_testing.md)" in index
        assert document.metadata.description in index
        assert document.content not in index
        print("索引包含链接和描述，不包含正文：通过")

        updated = document.model_copy(deep=True)
        updated.metadata.description = "测试结果必须有执行依据。"

        reopened.save(updated)

        index = reopened.read_index()
        assert index.count("(feedback_testing.md)") == 1
        assert updated.metadata.description in index
        print("同名更新，不产生重复索引：通过")

        reopened.delete(document.filename)

        assert reopened.list_documents() == []
        assert reopened.read_index() == ""
        assert not (root / document.filename).exists()
        print("删除主题，同时更新索引：通过")


if __name__ == "__main__":
    main()