from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.validation import (
    pending_tool_calls,
    require_complete_tools,
)


def make_entries(messages):
    entries = []

    for message in messages:
        entry = MessageEntry(
            session_id="demo-session",
            parent_id=entries[-1].id if entries else None,
            payload=message,
        )
        entries.append(entry)

    return entries


def main():
    batch = {
        "role": "assistant",
        "type": "tool_batch",
        "calls": [
            {
                "type": "tool_call",
                "id": "call_a",
                "name": "read_file",
                "arguments": {"path": "calculator.py"},
            },
            {
                "type": "tool_call",
                "id": "call_b",
                "name": "read_file",
                "arguments": {"path": "missing.py"},
            },
        ],
    }

    result_a = {
        "role": "tool",
        "tool_call_id": "call_a",
        "content": "文件内容",
        "is_error": False,
    }

    result_b = {
        "role": "tool",
        "tool_call_id": "call_b",
        "content": "文件不存在",
        "is_error": True,
    }

    partial = make_entries([batch, result_a])

    assert pending_tool_calls(partial) == {"call_b"}
    print("识别尚未返回的工具调用：通过")

    try:
        require_complete_tools(partial)
    except ValueError:
        print("拒绝未完成的工具交互：通过")
    else:
        raise AssertionError("缺少 B 的结果，不应通过")

    complete = make_entries([batch, result_a, result_b])

    require_complete_tools(complete)
    assert pending_tool_calls(complete) == set()
    print("失败结果也能完成调用配对：通过")

    # A 已经返回过一次，不能再次消费同一个调用。
    duplicate = make_entries([batch, result_a, result_a])

    try:
        pending_tool_calls(duplicate)
    except ValueError:
        print("拒绝重复工具结果：通过")
    else:
        raise AssertionError("重复结果不应通过")

    interrupted = make_entries([
        batch,
        result_a,
        {"role": "user", "content": "继续"},
    ])

    try:
        pending_tool_calls(interrupted)
    except ValueError:
        print("拒绝在未完成调用中插入新消息：通过")
    else:
        raise AssertionError("缺少 B 的结果时不能插入新消息")


if __name__ == "__main__":
    main()