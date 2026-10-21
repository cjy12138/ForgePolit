from pydantic import ValidationError

from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.summarization.schema import ContextSummary


def main():
    summary = ContextSummary(
        goal=[{
            "text": "解释项目结构",
            "source_entry_ids": ["node_a"],
        }],
        constraints=[],
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
        "parent_id": "node_d",
        "source_leaf_id": "node_d",
        "first_kept_entry_id": "node_c",
        "summary": summary,
    }

    entry = CompactionEntry(**data)

    restored = CompactionEntry.model_validate_json(
        entry.model_dump_json()
    )

    assert restored == entry
    assert restored.type == "compaction"
    assert restored.summary.goal[0].text == "解释项目结构"
    print("压缩节点和结构化摘要能够序列化并恢复：通过")

    try:
        CompactionEntry(**{
            **data,
            "parent_id": "another-node",
        })
    except ValidationError as exc:
        assert "来源终点" in str(exc)
    else:
        raise AssertionError("父节点与来源终点不一致时不应通过")

    print("拒绝来源边界不一致的压缩节点：通过")

    try:
        CompactionEntry(**{
            **data,
            "id": "node_c",
        })
    except ValidationError as exc:
        assert "引用自身" in str(exc)
    else:
        raise AssertionError("保留起点不能指向压缩节点自身")

    print("拒绝压缩节点引用自身作为历史边界：通过")

    try:
        CompactionEntry(**{
            **data,
            "summary": {"goal": []},
        })
    except ValidationError:
        pass
    else:
        raise AssertionError("不完整的结构化摘要不应通过")

    print("压缩节点会校验内部摘要结构：通过")


if __name__ == "__main__":
    main()