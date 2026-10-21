from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import CompactionEntry, MessageEntry
from forgepilot.sessions.summarization.schema import ContextSummary


def make_summary(text, source_id):
    return ContextSummary(
        goal=[{
            "text": text,
            "source_entry_ids": [source_id],
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


def main():
    a = MessageEntry(
        session_id="demo",
        payload={"role": "user", "content": "介绍项目"},
    )
    b = MessageEntry(
        session_id="demo",
        parent_id=a.id,
        payload={
            "role": "assistant",
            "type": "final",
            "content": "这是一个 Python Agent。",
        },
    )
    c = MessageEntry(
        session_id="demo",
        parent_id=b.id,
        payload={"role": "user", "content": "解释会话模块"},
    )
    d = MessageEntry(
        session_id="demo",
        parent_id=c.id,
        payload={
            "role": "assistant",
            "type": "final",
            "content": "会话使用树形结构。",
        },
    )

    k1 = CompactionEntry(
        session_id="demo",
        parent_id=d.id,
        source_leaf_id=d.id,
        first_kept_entry_id=c.id,
        summary=make_summary("第一份摘要：介绍项目", a.id),
    )

    e = MessageEntry(
        session_id="demo",
        parent_id=k1.id,
        payload={"role": "user", "content": "继续解释压缩"},
    )

    branch = [a, b, c, d, k1, e]
    before = [entry.model_dump() for entry in branch]

    messages = build_messages(branch)

    assert len(messages) == 4
    assert "第一份摘要" in messages[0]["content"]
    assert messages[1:] == [c.payload, d.payload, e.payload]
    assert [entry.model_dump() for entry in branch] == before

    print("摘要加保留原文加后续消息，且不修改历史：通过")

    k2 = CompactionEntry(
        session_id="demo",
        parent_id=e.id,
        source_leaf_id=e.id,
        first_kept_entry_id=e.id,
        summary=make_summary(
            "第二份摘要：已讨论项目和会话，现在解释压缩",
            a.id,
        ),
    )

    messages = build_messages([*branch, k2])

    assert len(messages) == 2
    assert "第二份摘要" in messages[0]["content"]
    assert "第一份摘要" not in messages[0]["content"]
    assert messages[1] == e.payload

    print("连续压缩只使用当前分支的最新摘要：通过")

    # 从 B 分叉，不能带入另一条路径上的 K1、K2。
    other = MessageEntry(
        session_id="demo",
        parent_id=b.id,
        payload={"role": "user", "content": "换个方向"},
    )

    assert build_messages([a, b, other]) == [
        a.payload,
        b.payload,
        other.payload,
    ]
    print("其他分支不会混入本分支的压缩摘要：通过")

    invalid = CompactionEntry(
        session_id="demo",
        parent_id=d.id,
        source_leaf_id=d.id,
        first_kept_entry_id="missing-node",
        summary=k1.summary,
    )

    try:
        build_messages([a, b, c, d, invalid])
    except ValueError as exc:
        assert "保留起点" in str(exc)
    else:
        raise AssertionError("不存在的保留起点不应通过")

    print("拒绝不存在的保留起点：通过")


if __name__ == "__main__":
    main()