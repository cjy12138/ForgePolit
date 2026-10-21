import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.memory.notes import SessionNotes, render_session_notes
from forgepilot.memory.notes_store import SessionNotesStore
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.session_notes import build_session_notes_tools


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        sessions.append_message({
            "role": "user",
            "content": "检查 calculator.py。",
        })
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "尚未运行测试。",
        })

        snapshot = build_memory_snapshot(
            sessions,
            require_final=False,
        )

        notes = SessionNotes(
            goal="检查 calculator.py。",
            verification=["尚未运行测试。"],
            next_steps=["读取实现并运行已有测试。"],
            source_entry_ids=[snapshot.user_entry_id],
        )

        notes_store = SessionNotesStore(sessions)
        notes_store.save(
            notes,
            snapshot=snapshot,
            history_tokens=10500,
        )

        registry = ToolRegistry(
            build_session_notes_tools(
                notes_store=notes_store,
            )
        )
        context = ToolContext(workspace=root)
        expected = render_session_notes(notes)
        history_before = sessions.path.read_bytes()

        result = registry.execute(
            "read_session_notes",
            {"offset": 0, "limit": 10},
            context=context,
        )

        assert result.ok
        first = json.loads(result.output)

        assert first["content"] == expected[:10]
        assert first["next_offset"] == 10
        assert first["source_leaf_id"] == snapshot.source_leaf_id
        assert first["history_tokens"] == 10500

        result = registry.execute(
            "read_session_notes",
            {
                "offset": first["next_offset"],
                "limit": 8000,
            },
            context=context,
        )

        assert result.ok
        rest = json.loads(result.output)

        assert first["content"] + rest["content"] == expected
        assert rest["next_offset"] is None
        assert sessions.path.read_bytes() == history_before

        print("工具注册、分段续读和来源信息：通过")

        result = registry.execute(
            "read_session_notes",
            {"offset": len(expected) + 1},
            context=context,
        )

        assert not result.ok
        assert "超出笔记长度" in result.output
        print("拒绝超出范围的偏移：通过")

        # 没有文件名参数，不能指定其他会话文件。
        result = registry.execute(
            "read_session_notes",
            {"filename": "../other-session/session_notes.md"},
            context=context,
        )

        assert not result.ok
        print("拒绝额外文件路径参数：通过")

        # 创建不包含原笔记来源终点的新分支。
        sessions.move_cursor(snapshot.user_entry_id)
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "另一条分支先讨论方案。",
        })

        result = registry.execute(
            "read_session_notes",
            {},
            context=context,
        )

        assert not result.ok
        assert "没有可用的会话笔记" in result.output
        print("不相关分支拒绝读取旧笔记：通过")


if __name__ == "__main__":
    main()