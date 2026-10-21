from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import MessageEntry


def make_entries(messages):
    entries = []

    for message in messages:
        entries.append(MessageEntry(
            session_id="demo",
            parent_id=entries[-1].id if entries else None,
            payload=message,
        ))

    return entries


def main():
    entries = make_entries([
        {
            "role": "user",
            "content": "读取两个文件",
        },
        {
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
        },
        {
            "role": "tool",
            "tool_call_id": "call_a",
            "content": "a.py 的内容",
            "is_error": False,
        },
        {
            "role": "tool",
            "tool_call_id": "call_b",
            "content": "b.py 不存在",
            "is_error": True,
        },
        {
            "role": "assistant",
            "type": "final",
            "content": "已读取 a.py，b.py 不存在。",
        },
    ])

    before = [entry.model_dump() for entry in entries]
    groups = group_entries(entries)

    assert [
        [entry.id for entry in group]
        for group in groups
    ] == [
        [entries[0].id],
        [entries[1].id, entries[2].id, entries[3].id],
        [entries[4].id],
    ]
    print("批量调用与所有结果保存在同一组：通过")

    flattened = [
        entry
        for group in groups
        for entry in group
    ]

    assert [entry.id for entry in flattened] == [
        entry.id for entry in entries
    ]
    assert [entry.model_dump() for entry in entries] == before
    print("分组不丢失、不重排、不修改消息：通过")

    # 截到 A 的结果，缺少 B。
    try:
        group_entries(entries[:3])
    except ValueError as exc:
        assert "未完成的工具调用" in str(exc)
    else:
        raise AssertionError("未完成的工具交互不能进入压缩分组")

    print("拒绝对缺少工具结果的历史进行分组：通过")

    single = make_entries([
        {
            "role": "assistant",
            "type": "tool_call",
            "id": "single",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        },
        {
            "role": "tool",
            "tool_call_id": "single",
            "content": "README 内容",
            "is_error": False,
        },
    ])

    assert group_entries(single) == [single]
    assert group_entries([]) == []
    print("单工具调用与空历史处理正确：通过")


if __name__ == "__main__":
    main()