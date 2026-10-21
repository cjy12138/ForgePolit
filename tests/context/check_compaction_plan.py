from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.validation import require_complete_tools


def main():
    entries = []

    def add(payload):
        entries.append(MessageEntry(
            session_id="demo",
            parent_id=entries[-1].id if entries else None,
            payload=payload,
        ))

    add({
        "role": "user",
        "content": "读取文件，只读不修改。",
    })

    add({
        "role": "assistant",
        "type": "tool_call",
        "id": "call_a",
        "name": "read_file",
        "arguments": {"path": "README.md"},
    })

    add({
        "role": "tool",
        "tool_call_id": "call_a",
        "content": "README 内容",
        "is_error": False,
    })

    add({
        "role": "assistant",
        "type": "final",
        "content": "已读取。",
    })

    before = [entry.model_dump() for entry in entries]

    # 三组：用户、工具交互、最终回答。
    # 保留最后两组，只将第一组列入摘要范围。
    plan = plan_compaction(
        entries,
        keep_recent_groups=2,
    )

    assert plan is not None
    assert plan.to_summarize == entries[:1]
    assert plan.to_keep == entries[1:]
    assert plan.source_leaf_id == entries[-1].id
    assert plan.first_kept_entry_id == entries[1].id

    require_complete_tools(plan.to_summarize)
    require_complete_tools(plan.to_keep)
    print("保留边界不会拆开工具调用和结果：通过")

    # 只保留最后一组，完整工具交互进入摘要范围。
    plan = plan_compaction(
        entries,
        keep_recent_groups=1,
    )

    assert plan is not None
    assert plan.to_summarize == entries[:3]
    assert plan.to_keep == entries[3:]

    require_complete_tools(plan.to_summarize)
    require_complete_tools(plan.to_keep)
    print("压缩范围也保持工具交互完整：通过")

    assert (
        plan.to_summarize + plan.to_keep
        == entries
    )
    assert [entry.model_dump() for entry in entries] == before
    print("切分不丢失、不重排、不修改原始消息：通过")

    assert plan_compaction(
        entries,
        keep_recent_groups=3,
    ) is None

    assert plan_compaction(
        [],
        keep_recent_groups=1,
    ) is None
    print("历史不足时不生成压缩计划：通过")

    for invalid in (0, -1, True, 1.5):
        try:
            plan_compaction(
                entries,
                keep_recent_groups=invalid,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"错误参数未被拒绝：{invalid}")

    print("拒绝不合法的保留量：通过")


if __name__ == "__main__":
    main()