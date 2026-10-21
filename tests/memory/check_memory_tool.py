import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.memory.models import (
    MemoryDocument,
    MemoryMetadata,
)
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.memory import build_memory_tools


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = MarkdownMemoryStore(root / "memory")

        store.save(MemoryDocument(
            filename="user_preferences.md",
            metadata=MemoryMetadata(
                name="用户偏好",
                type="user",
                description="主要使用 Python。",
                source_session_id="demo-session",
                source_entry_ids=["demo-entry"],
            ),
            content="主要使用 Python，希望分步骤学习。",
        ))

        registry = ToolRegistry(
            build_memory_tools(memory_store=store)
        )
        context = ToolContext(workspace=root)

        result = registry.execute(
            "read_memory",
            {
                "filename": "user_preferences.md",
                "offset": 0,
                "limit": 4,
            },
            context=context,
        )

        assert result.ok
        data = json.loads(result.output)
        assert data["content"] == "主要使用"
        assert data["next_offset"] == 4
        assert len(data["document_sha256"]) == 64
        print("工具注册、分段读取及内容哈希：通过")

        result = registry.execute(
            "read_memory",
            {"filename": "../private.md"},
            context=context,
        )

        assert not result.ok
        print("拒绝越界文件名：通过")

        result = registry.execute(
            "glob_memory",
            {"pattern": "user_*.md"},
            context=context,
        )

        assert result.ok
        data = json.loads(result.output)
        assert data["files"] == ["user_preferences.md"]
        assert data["total_matches"] == 1
        assert data["truncated"] is False
        print("按模式发现主题文件：通过")

        result = registry.execute(
            "glob_memory",
            {"pattern": "feedback_*.md"},
            context=context,
        )

        assert result.ok
        assert json.loads(result.output)["files"] == []
        print("没有匹配时返回空列表：通过")

        result = registry.execute(
            "glob_memory",
            {"pattern": "../*.md"},
            context=context,
        )

        assert not result.ok
        print("拒绝目录路径参数：通过")


if __name__ == "__main__":
    main()