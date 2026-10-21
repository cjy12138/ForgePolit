from pydantic import ValidationError

from forgepilot.sessions.snip.records import (
    SnipRecord,
    hash_tool_payload,
)


def main():
    original = {
        "role": "tool",
        "tool_call_id": "call_a",
        "content": "完整输出",
        "is_error": False,
        "exit_code": 0,
    }

    source_hash = hash_tool_payload(original)

    record = SnipRecord(
        session_id="session_a",
        source_leaf_id="leaf_a",
        target_entry_id="result_a",
        source_sha256=source_hash,
    )

    restored = SnipRecord.model_validate_json(
        record.model_dump_json()
    )
    assert restored == record
    print("记录序列化与恢复：通过")

    reordered = dict(reversed(list(original.items())))
    assert hash_tool_payload(reordered) == source_hash

    changed = {**original, "content": "被修改的输出"}
    assert hash_tool_payload(changed) != source_hash

    changed_status = {**original, "exit_code": 1}
    assert hash_tool_payload(changed_status) != source_hash
    print("来源哈希校验：通过")

    for updates in (
        {"head_chars": 0, "tail_chars": 0},
        {"head_chars": -1},
        {"head_chars": True},
        {"source_sha256": "invalid"},
    ):
        data = {
            **record.model_dump(),
            **updates,
        }

        try:
            SnipRecord.model_validate(data)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"错误参数被接受：{updates}")

    print("拒绝非法参数：通过")


if __name__ == "__main__":
    main()