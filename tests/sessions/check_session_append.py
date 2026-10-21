from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory(prefix="forgepilot-append-") as directory:
        path = Path(directory) / "session.jsonl"
        store = SessionStore(path)

        store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        a = store.append_message({
            "role": "user",
            "content": "解释加法函数",
        })

        b = store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "它返回两个参数的和。",
        })

        c = store.append_message({
            "role": "user",
            "content": "举一个正数例子",
        })

        assert a.parent_id is None
        assert b.parent_id == a.id
        assert c.parent_id == b.id
        print("消息自动接到当前节点：通过")

        # 回到 B，再提出不同的问题。
        store.move_cursor(b.id)

        d = store.append_message({
            "role": "user",
            "content": "举一个负数例子",
        })

        assert d.parent_id == b.id

        assert [
            entry.id
            for entry in store.get_current_branch()
        ] == [a.id, b.id, d.id]

        # 原来的分支仍然存在。
        assert [
            entry.id
            for entry in store.get_branch(c.id)
        ] == [a.id, b.id, c.id]

        print("切换后追加形成新分支：通过")

        # 重新打开，再追加回答。
        reopened = SessionStore(path)

        e = reopened.append_message({
            "role": "assistant",
            "type": "final",
            "content": "例如 add(-2, 3) 返回 1。",
        })

        assert e.parent_id == d.id

        assert [
            entry.id
            for entry in reopened.get_current_branch()
        ] == [a.id, b.id, d.id, e.id]

        print("重新打开后沿当前分支继续：通过")


if __name__ == "__main__":
    main()