from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.models import MessageEntry, SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory(prefix="forgepilot-session-") as directory:
        path = Path(directory) / "session.jsonl"

        header = SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        )

        store = SessionStore(path)
        store.create(header)

        first = MessageEntry(
            session_id=header.session_id,
            payload={
                "role": "user",
                "content": "解释加法函数",
            },
        )

        second = MessageEntry(
            session_id=header.session_id,
            parent_id=first.id,
            payload={
                "role": "assistant",
                "type": "final",
                "content": "它返回两个参数的和。",
            },
        )

        store.append(first)
        store.append(second)

        # 新建对象，从磁盘读回，而不是使用原来的内存对象。
        reopened = SessionStore(path)
        loaded_header, entries = reopened.read()

        assert loaded_header == header
        assert entries == [first, second]
        print("会话保存并重新读回：通过")

        before = path.read_bytes()

        invalid = MessageEntry(
            session_id=header.session_id,
            parent_id="missing-parent",
            payload={"role": "user", "content": "继续"},
        )

        try:
            reopened.append(invalid)
        except ValueError as exc:
            assert "父节点" in str(exc)
        else:
            raise AssertionError("不存在的父节点不应被接受")

        assert path.read_bytes() == before
        print("拒绝无效父节点且文件不变：通过")

        try:
            reopened.create(header)
        except FileExistsError:
            pass
        else:
            raise AssertionError("不应该覆盖已有会话")

        assert path.read_bytes() == before
        print("拒绝覆盖已有会话：通过")


if __name__ == "__main__":
    main()