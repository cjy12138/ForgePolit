from forgepilot.sessions.models import MessageEntry, new_id


def main():
    session_id = new_id()

    user_entry = MessageEntry(
        session_id=session_id,
        payload={
            "role": "user",
            "content": "读取 calculator.py",
        },
    )

    call_entry = MessageEntry(
        session_id=session_id,
        parent_id=user_entry.id,
        payload={
            "role": "assistant",
            "type": "tool_call",
            "id": "call_1",
            "name": "read_file",
            "arguments": {"path": "calculator.py"},
        },
    )

    result_entry = MessageEntry(
        session_id=session_id,
        parent_id=call_entry.id,
        payload={
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "def add(a, b): return a + b",
            "is_error": False,
            "exit_code": None,
        },
    )

    assert user_entry.parent_id is None
    assert call_entry.parent_id == user_entry.id
    assert result_entry.parent_id == call_entry.id

    assert len({
        user_entry.id,
        call_entry.id,
        result_entry.id,
    }) == 3

    assert (
        result_entry.payload["tool_call_id"]
        == call_entry.payload["id"]
    )

    print("消息节点与父子关联：通过")

    # 模拟保存为 JSON，再恢复为对象。
    serialized = result_entry.model_dump_json()
    restored = MessageEntry.model_validate_json(serialized)

    assert restored == result_entry
    print("消息节点 JSON 往返：通过")

    print(serialized)


if __name__ == "__main__":
    main()