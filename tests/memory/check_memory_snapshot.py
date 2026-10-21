from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = SessionStore(root / "session.jsonl")

        header = SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        )
        store.create(header)

        store.append_message({
            "role": "user",
            "content": "以后优先使用 unittest。",
        })

        # 尚未结束，不能提取。
        try:
            build_memory_snapshot(store)
        except ValueError as exc:
            assert "尚未完成" in str(exc)
        else:
            raise AssertionError("应拒绝未完成轮次")

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "好的，后续优先使用 unittest。",
        })

        before = store.path.read_bytes()
        snapshot = build_memory_snapshot(store)

        assert snapshot.session_id == header.session_id
        assert len(snapshot.entries) == 2
        assert snapshot.user_entry_id == snapshot.entries[0].id

        snapshot.entries[0].payload["content"] = "只修改副本"

        assert (
            store.get_current_branch()[0].payload["content"]
            == "以后优先使用 unittest。"
        )
        assert store.path.read_bytes() == before

        print("拒绝未完成轮次：通过")
        print("快照来源与消息复制：通过")
        print("创建和修改快照不改变会话历史：通过")


if __name__ == "__main__":
    main()