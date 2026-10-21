from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.notes import SessionNotes
from forgepilot.memory.notes_store import SessionNotesStore
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


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
            "content": "修复 calculator.py，不能修改测试文件。",
        })
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "尚未修改代码，也未运行测试。",
        })

        snapshot = build_memory_snapshot(
            sessions,
            require_final=False,
        )

        notes = SessionNotes(
            goal="修复 calculator.py。",
            constraints=["不能修改测试文件。"],
            progress=["尚未修改代码。"],
            verification=["尚未运行测试。"],
            next_steps=["读取实现并运行已有测试。"],
            source_entry_ids=[
                snapshot.user_entry_id,
                snapshot.entries[-1].id,
            ],
        )

        store = SessionNotesStore(sessions)
        history_before = sessions.path.read_bytes()

        saved = store.save(
            notes,
            snapshot=snapshot,
            history_tokens=10500,
        )

        assert store.markdown_path.is_file()
        assert store.state_path.is_file()

        markdown = store.markdown_path.read_text(
            encoding="utf-8"
        )
        assert "修复 calculator.py" in markdown
        assert "尚未运行测试" in markdown

        reopened = SessionNotesStore(sessions)
        current = reopened.get_current()

        assert current is not None
        assert current == saved
        assert current.history_tokens == 10500
        assert sessions.path.read_bytes() == history_before

        print("保存 Markdown、状态和重新读取：通过")

        # 在原分支继续追加消息，旧笔记仍是可用的早期参考。
        sessions.append_message({
            "role": "user",
            "content": "继续检查实现。",
        })

        assert reopened.get_current() is not None
        print("来源仍在祖先链上时笔记可用：通过")

        # 回到旧用户节点，从这里创建另一条分支。
        sessions.move_cursor(snapshot.user_entry_id)
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "这一分支先讨论修复方案。",
        })

        assert reopened.get_current() is None
        print("切换到不相关分支后旧笔记不可用：通过")

        markdown_before = store.markdown_path.read_bytes()
        state_before = store.state_path.read_bytes()

        # 原快照的来源终点不在新分支上，不能覆盖笔记。
        try:
            reopened.save(
                notes,
                snapshot=snapshot,
                history_tokens=20000,
            )
        except ValueError as exc:
            assert "不在当前分支" in str(exc)
        else:
            raise AssertionError("应拒绝失效快照")

        assert store.markdown_path.read_bytes() == markdown_before
        assert store.state_path.read_bytes() == state_before

        print("失效快照不写入，也不推进水位：通过")


if __name__ == "__main__":
    main()