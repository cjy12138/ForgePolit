from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.models import MessageEntry, SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory(prefix="forgepilot-branch-") as directory:
        path = Path(directory) / "session.jsonl"

        header = SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        )

        store = SessionStore(path)
        store.create(header)

        a = MessageEntry(
            session_id=header.session_id,
            payload={"role": "user", "content": "解释加法函数"},
        )

        b = MessageEntry(
            session_id=header.session_id,
            parent_id=a.id,
            payload={
                "role": "assistant",
                "type": "final",
                "content": "它返回两个参数的和。",
            },
        )

        c = MessageEntry(
            session_id=header.session_id,
            parent_id=b.id,
            payload={"role": "user", "content": "补充一个正数例子"},
        )

        d = MessageEntry(
            session_id=header.session_id,
            parent_id=c.id,
            payload={
                "role": "assistant",
                "type": "final",
                "content": "例如 add(2, 3) 返回 5。",
            },
        )

        # 回到 B，从这里提出另一个问题。
        e = MessageEntry(
            session_id=header.session_id,
            parent_id=b.id,
            payload={"role": "user", "content": "解释负数相加"},
        )

        for entry in [a, b, c, d, e]:
            store.append(entry)

        before = path.read_bytes()

        old_branch = store.get_branch(d.id)
        new_branch = store.get_branch(e.id)

        assert [entry.id for entry in old_branch] == [
            a.id, b.id, c.id, d.id,
        ]

        assert [entry.id for entry in new_branch] == [
            a.id, b.id, e.id,
        ]

        assert path.read_bytes() == before
        print("两个分支互不混入，查询不修改历史：通过")

        reopened = SessionStore(path)

        assert [
            entry.id
            for entry in reopened.get_branch(e.id)
        ] == [a.id, b.id, e.id]

        print("重新打开后仍能查询分支：通过")

        try:
            reopened.get_branch("missing-node")
        except ValueError:
            print("拒绝不存在的节点：通过")
        else:
            raise AssertionError("不存在的节点不应查询成功")

        # E 最后写入，默认选中 E。
        assert [
            entry.id
            for entry in store.get_current_branch()
        ] == [a.id, b.id, e.id]

        before_switch = path.read_bytes()

        store.move_cursor(d.id)

        # 切换仅追加记录，旧文件内容保持为前缀。
        assert path.read_bytes().startswith(before_switch)

        reopened = SessionStore(path)

        assert [
            entry.id
            for entry in reopened.get_current_branch()
        ] == [a.id, b.id, c.id, d.id]

        print("切换后重新打开，当前分支仍正确：通过")

        # 游标事件不能混入消息列表。
        _, entries = reopened.read()
        assert len(entries) == 5

        # E 分支仍然存在。
        assert [
            entry.id
            for entry in reopened.get_branch(e.id)
        ] == [a.id, b.id, e.id]

        print("游标事件与消息分离，旧分支保留：通过")


if __name__ == "__main__":
    main()