from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


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

        root = store.append_message({
            "role": "user",
            "content": "介绍这个项目",
        })

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "这是旧分支的回答。",
        })

        # 回到根消息，建立另一条分支。
        store.move_cursor(root.id)

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "这是新分支的回答。",
        })

        before = store.path.read_bytes()
        entries = store.get_current_branch()
        messages = build_messages(entries)

        assert messages == [
            {
                "role": "user",
                "content": "介绍这个项目",
            },
            {
                "role": "assistant",
                "type": "final",
                "content": "这是新分支的回答。",
            },
        ]

        assert store.path.read_bytes() == before
        print("仅提取当前分支，且不修改会话文件：通过")

        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "call_a",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        })

        try:
            build_messages(store.get_current_branch())
        except ValueError as exc:
            assert "未完成的工具调用" in str(exc)
        else:
            raise AssertionError("缺少工具结果时，不应构建模型历史")

        print("拒绝包含未完成工具调用的历史：通过")

        store.append_message({
            "role": "tool",
            "tool_call_id": "call_a",
            "content": "README 的内容",
            "is_error": False,
        })

        entries = store.get_current_branch()
        messages = build_messages(entries)

        assert messages[-2]["id"] == "call_a"
        assert messages[-1]["tool_call_id"] == "call_a"
        print("完整保留工具调用与工具结果：通过")

        # 修改构建出的消息，不应该影响原始节点。
        messages[-2]["arguments"]["path"] = "changed.md"

        assert (
            entries[-2].payload["arguments"]["path"]
            == "README.md"
        )
        print("嵌套消息与原始节点相互独立：通过")


if __name__ == "__main__":
    main()