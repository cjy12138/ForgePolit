from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def expect_rejected(store, target_id):
    before = store.path.read_bytes()
    _, _, old_cursor = store.replay()

    try:
        store.move_cursor(target_id)
    except ValueError as exc:
        assert "未完成的工具调用" in str(exc)
    else:
        raise AssertionError("不完整的工具交互不应允许切换")

    assert store.path.read_bytes() == before

    _, _, new_cursor = store.replay()
    assert new_cursor == old_cursor


def main():
    with TemporaryDirectory() as directory:
        store = SessionStore(
            Path(directory) / "session.jsonl"
        )

        store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        user = store.append_message({
            "role": "user",
            "content": "读取两个文件",
        })

        batch = store.append_message({
            "role": "assistant",
            "type": "tool_batch",
            "calls": [
                {
                    "type": "tool_call",
                    "id": "call_a",
                    "name": "read_file",
                    "arguments": {"path": "a.py"},
                },
                {
                    "type": "tool_call",
                    "id": "call_b",
                    "name": "read_file",
                    "arguments": {"path": "b.py"},
                },
            ],
        })

        result_a = store.append_message({
            "role": "tool",
            "tool_call_id": "call_a",
            "content": "a.py 的内容",
            "is_error": False,
        })

        # 目标 user 是完整位置，但当前还缺 B 的结果。
        expect_rejected(store, user.id)
        print("当前工具结果未收齐，拒绝切走：通过")

        result_b = store.append_message({
            "role": "tool",
            "tool_call_id": "call_b",
            "content": "b.py 不存在",
            "is_error": True,
        })

        final = store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "已读取 a.py，b.py 不存在。",
        })

        # 当前完整，但目标位置的工具结果尚未收齐。
        expect_rejected(store, batch.id)
        expect_rejected(store, result_a.id)
        print("拒绝切到工具交互中间，文件和游标不变：通过")

        # B 即使执行失败，也已经有真实结果，配对完整。
        store.move_cursor(result_b.id)
        assert store.get_current_branch()[-1].id == result_b.id
        print("允许切到工具结果收齐的位置：通过")

        store.move_cursor(user.id)

        new_message = store.append_message({
            "role": "user",
            "content": "换个任务，解释这个项目。",
        })

        assert new_message.parent_id == user.id

        # 切换和创建分支不会删除原来的历史。
        old_branch = store.get_branch(final.id)
        assert [entry.id for entry in old_branch] == [
            user.id,
            batch.id,
            result_a.id,
            result_b.id,
            final.id,
        ]

        reopened = SessionStore(store.path)
        assert (
            reopened.get_current_branch()[-1].id
            == new_message.id
        )
        print("完整位置可创建分支，旧历史保留且可重新打开：通过")


if __name__ == "__main__":
    main()