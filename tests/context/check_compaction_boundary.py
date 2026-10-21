from forgepilot.sessions.compaction.planning import validate_compaction_entry
from forgepilot.sessions.models import CompactionEntry, MessageEntry
from forgepilot.sessions.summarization.schema import ContextSummary


def main():
    branch = []

    def add(payload):
        branch.append(MessageEntry(
            session_id="demo",
            parent_id=branch[-1].id if branch else None,
            payload=payload,
        ))

    add({
        "role": "user",
        "content": "读取 README.md，只读不修改。",
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

    a, b, c, d = branch

    summary = ContextSummary(
        goal=[{
            "text": "读取 README.md",
            "source_entry_ids": [a.id],
        }],
        constraints=[{
            "text": "只读不修改",
            "source_entry_ids": [a.id],
        }],
        completed=[],
        in_progress=[],
        blockers=[],
        decisions=[],
        next_steps=[],
        verification=[],
        critical_context=[],
    )

    data = {
        "session_id": "demo",
        "parent_id": d.id,
        "source_leaf_id": d.id,
        "first_kept_entry_id": b.id,
        "summary": summary,
    }

    entry = CompactionEntry(**data)
    validate_compaction_entry(entry, branch)
    print("合法摘要范围与保留边界通过：通过")

    def expect_rejected(changes, expected):
        candidate = CompactionEntry(**{
            **data,
            **changes,
        })

        try:
            validate_compaction_entry(candidate, branch)
        except ValueError as exc:
            assert expected in str(exc), str(exc)
        else:
            raise AssertionError("非法压缩边界不应通过")

    expect_rejected(
        {"first_kept_entry_id": c.id},
        "完整交互组",
    )
    print("拒绝把工具结果单独留在保留区：通过")

    expect_rejected(
        {"first_kept_entry_id": "another-branch-node"},
        "完整交互组",
    )
    print("拒绝使用当前分支之外的保留起点：通过")

    expect_rejected(
        {
            "parent_id": b.id,
            "source_leaf_id": b.id,
        },
        "来源终点",
    )
    print("拒绝基于过时终点发布摘要：通过")

    expect_rejected(
        {"first_kept_entry_id": a.id},
        "没有需要压缩",
    )
    print("没有旧消息需要压缩时拒绝发布：通过")

    wrong_summary = summary.model_dump()
    wrong_summary["goal"][0]["source_entry_ids"] = [d.id]

    expect_rejected(
        {"summary": ContextSummary.model_validate(wrong_summary)},
        "摘要范围外",
    )
    print("拒绝用保留区消息充当旧历史摘要的来源：通过")


if __name__ == "__main__":
    main()