from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        store = SessionStore(path)

        store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        store.append_message({
            "role": "user",
            "content": "读取 README.md",
        })

        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "call_a",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        })

        assert pending_tool_calls(
            store.get_current_branch()
        ) == {"call_a"}
        print("允许保存尚未收到结果的工具调用：通过")

        before = path.read_bytes()

        try:
            store.append_message({
                "role": "user",
                "content": "继续",
            })
        except ValueError as exc:
            assert "工具结果尚未收齐" in str(exc)
        else:
            raise AssertionError("工具结果未返回，不应允许插入用户消息")

        assert path.read_bytes() == before
        print("拒绝错误消息，且会话文件不变：通过")

        store.append_message({
            "role": "tool",
            "tool_call_id": "call_a",
            "content": "README 文件内容",
            "is_error": False,
        })

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "已读取 README.md。",
        })

        # 重新打开文件，确认实际保存的历史仍然正确。
        reopened = SessionStore(path)
        branch = reopened.get_current_branch()

        assert len(branch) == 4
        assert pending_tool_calls(branch) == set()
        print("补齐结果后可继续，重新打开会话校验通过")


if __name__ == "__main__":
    main()