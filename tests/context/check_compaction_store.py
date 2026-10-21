from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import CompactionEntry, SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.schema import ContextSummary


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        store = SessionStore(path)
        header = SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        )
        store.create(header)

        a = store.append_message({
            "role": "user",
            "content": "介绍项目",
        })
        b = store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "这是 Python Agent。",
        })
        c = store.append_message({
            "role": "user",
            "content": "继续介绍会话",
        })

        summary = ContextSummary(
            goal=[{
                "text": "用户希望了解项目",
                "source_entry_ids": [a.id],
            }],
            constraints=[],
            completed=[],
            in_progress=[],
            blockers=[],
            decisions=[],
            next_steps=[],
            verification=[],
            critical_context=[],
        )

        k = CompactionEntry(
            session_id=header.session_id,
            parent_id=c.id,
            source_leaf_id=c.id,
            first_kept_entry_id=c.id,
            summary=summary,
        )

        before = path.read_bytes()
        store.append(k)

        assert path.read_bytes().startswith(before)

        reopened = SessionStore(path)
        branch = reopened.get_current_branch()

        assert [node.id for node in branch] == [
            a.id, b.id, c.id, k.id,
        ]
        assert isinstance(branch[-1], CompactionEntry)

        messages = build_messages(branch)
        assert len(messages) == 2
        assert messages[-1] == c.payload
        print("压缩节点落盘、重放和上下文构建通过")

        d = reopened.append_message({
            "role": "assistant",
            "type": "final",
            "content": "会话使用树形结构。",
        })

        assert d.parent_id == k.id
        assert len(build_messages(
            reopened.get_current_branch()
        )) == 3
        print("新消息可以接在压缩节点之后：通过")

        reopened.move_cursor(b.id)

        assert build_messages(
            reopened.get_current_branch()
        ) == [a.payload, b.payload]

        assert reopened.get_branch(d.id)[-1].id == d.id
        print("切回旧分支不带摘要，压缩分支仍保留：通过")

        stale = CompactionEntry(
            session_id=header.session_id,
            parent_id=c.id,
            source_leaf_id=c.id,
            first_kept_entry_id=c.id,
            summary=summary,
        )

        before = path.read_bytes()

        try:
            reopened.append(stale)
        except ValueError as exc:
            assert "位置已变化" in str(exc)
        else:
            raise AssertionError("不能向已经离开的分支发布摘要")

        assert path.read_bytes() == before
        print("拒绝向过时位置发布摘要，文件不变：通过")


if __name__ == "__main__":
    main()