from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.models import MemoryCandidate, MemoryProposal
from forgepilot.memory.publication import publish_new_memories
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.memory.store import MarkdownMemoryStore
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
            "content": "以后优先使用 unittest。",
        })
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "好的。",
        })

        snapshot = build_memory_snapshot(sessions)
        memory_store = MarkdownMemoryStore(root / "memory")

        proposal = MemoryProposal(memories=[
            MemoryCandidate(
                filename="testing_preferences.md",
                name="测试偏好",
                type="feedback",
                description="用户要求优先使用 unittest。",
                content="编写 Python 测试时优先使用 unittest。",
                source_entry_ids=[snapshot.user_entry_id],
            )
        ])

        before = sessions.path.read_bytes()

        documents = publish_new_memories(
            proposal,
            snapshot=snapshot,
            sessions=sessions,
            memory_store=memory_store,
        )

        saved = memory_store.read(
            "testing_preferences.md"
        )

        assert len(documents) == 1
        assert saved.metadata.source_session_id == (
            snapshot.session_id
        )
        assert saved.metadata.source_entry_ids == [
            snapshot.user_entry_id
        ]
        assert "unittest" in saved.content

        index = memory_store.read_index()
        assert "testing_preferences.md" in index
        assert "用户要求优先使用 unittest。" in index

        assert sessions.path.read_bytes() == before
        print("新增主题、真实来源和索引更新：通过")

        topic_path = memory_store.root / "testing_preferences.md"
        topic_before = topic_path.read_bytes()

        try:
            publish_new_memories(
                proposal,
                snapshot=snapshot,
                sessions=sessions,
                memory_store=memory_store,
            )
        except ValueError as exc:
            assert "已存在" in str(exc)
        else:
            raise AssertionError("不应直接覆盖已有主题")

        assert topic_path.read_bytes() == topic_before
        print("拒绝直接覆盖已有主题：通过")


if __name__ == "__main__":
    main()