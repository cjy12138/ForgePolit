from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.snip.records import (
    SnipRecord,
    hash_tool_payload,
)
from forgepilot.sessions.snip.validation import validate_snip_record


def main():
    call = MessageEntry(
        session_id="session_a",
        payload={
            "role": "assistant",
            "type": "tool_call",
            "id": "call_a",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        },
    )

    result = MessageEntry(
        session_id="session_a",
        parent_id=call.id,
        payload={
            "role": "tool",
            "tool_call_id": "call_a",
            "content": "x" * 10000,
            "is_error": False,
            "exit_code": 0,
            "tool_name": "read_file",
            "artifact": {
                "id": "a" * 32,
                "sha256": "b" * 64,
                "size_bytes": 10000,
            },
        },
    )

    branch = [call, result]

    record = SnipRecord(
        session_id="session_a",
        source_leaf_id=result.id,
        target_entry_id=result.id,
        source_sha256=hash_tool_payload(result.payload),
    )

    assert validate_snip_record(record, branch) is result
    print("接受有效记录：通过")

    later = MessageEntry(
        session_id="session_a",
        parent_id=result.id,
        payload={"role": "user", "content": "继续"},
    )

    assert validate_snip_record(
        record,
        [*branch, later],
    ) is result
    print("尾部新增消息不使记录失效：通过")

    def expect_rejected(candidate, nodes):
        try:
            validate_snip_record(candidate, nodes)
        except ValueError:
            return

        raise AssertionError("错误记录被接受")

    def changed_record(**updates):
        return SnipRecord.model_validate({
            **record.model_dump(),
            **updates,
        })

    expect_rejected(
        changed_record(session_id="other_session"),
        branch,
    )
    expect_rejected(
        changed_record(source_leaf_id="missing"),
        branch,
    )
    expect_rejected(
        changed_record(target_entry_id=call.id),
        branch,
    )

    # 锚点处只有调用，尚未收到工具结果。
    expect_rejected(
        changed_record(source_leaf_id=call.id),
        branch,
    )

    changed_result = result.model_copy(deep=True)
    changed_result.payload["content"] += "修改"

    expect_rejected(record, [call, changed_result])
    print("拒绝会话、锚点、目标与来源冲突：通过")


if __name__ == "__main__":
    main()