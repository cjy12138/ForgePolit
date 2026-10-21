from forgepilot.sessions.inspection import inspect_branch
from forgepilot.sessions.models import MessageEntry


def main():
    entries = []

    def add(payload):
        entries.append(MessageEntry(
            session_id="demo",
            parent_id=entries[-1].id if entries else None,
            payload=payload,
        ))

    assert inspect_branch(entries) == "empty"

    add({
        "role": "user",
        "content": "读取文件",
    })
    assert inspect_branch(entries) == "ready"

    add({
        "role": "assistant",
        "type": "tool_call",
        "id": "call_a",
        "name": "read_file",
        "arguments": {"path": "README.md"},
    })
    assert inspect_branch(entries) == "needs_recovery"

    add({
        "role": "tool",
        "tool_call_id": "call_a",
        "content": "文件不存在",
        "is_error": True,
    })
    assert inspect_branch(entries) == "ready"

    add({
        "role": "assistant",
        "type": "final",
        "content": "文件不存在，无法读取。",
    })
    assert inspect_branch(entries) == "completed"

    print("空会话、待执行、中断、工具返回和最终回答状态：通过")


if __name__ == "__main__":
    main()