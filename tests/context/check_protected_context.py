from forgepilot.sessions.context import (
    build_messages,
    build_request_messages,
)
from forgepilot.sessions.models import (
    CompactionEntry,
    MessageEntry,
)
from forgepilot.sessions.summarization.schema import ContextSummary


def main():
    a = MessageEntry(
        session_id="demo",
        payload={
            "role": "user",
            "content": "只修改 calculator.py，不修改测试文件。",
        },
    )

    b = MessageEntry(
        session_id="demo",
        parent_id=a.id,
        payload={
            "role": "assistant",
            "type": "final",
            "content": "准备检查代码。",
        },
    )

    c = MessageEntry(
        session_id="demo",
        parent_id=b.id,
        payload={
            "role": "user",
            "content": "完成后运行 unittest。",
        },
    )

    # 故意构造没有保存任何限制的摘要。
    summary = ContextSummary(**{
        name: []
        for name in ContextSummary.model_fields
    })

    k = CompactionEntry(
        session_id="demo",
        parent_id=c.id,
        source_leaf_id=c.id,
        first_kept_entry_id=c.id,
        summary=summary,
    )

    branch = [a, b, c, k]
    before = [entry.model_dump() for entry in branch]

    history = build_messages(branch)
    request = build_request_messages(branch)

    assert request == [a.payload, *history]
    assert request.count(a.payload) == 1
    assert request.count(c.payload) == 1
    assert request[0]["role"] == "user"
    print("摘要遗漏限制时仍保留原文，且不重复保留区消息：通过")

    assert [entry.model_dump() for entry in branch] == before

    # 修改组装结果，也不应影响存储节点。
    request[0]["content"] = "修改返回值"
    assert a.payload["content"] == (
        "只修改 calculator.py，不修改测试文件。"
    )
    print("请求组装不修改原始历史：通过")

    d = MessageEntry(
        session_id="demo",
        parent_id=k.id,
        payload={
            "role": "assistant",
            "type": "final",
            "content": "等待执行测试。",
        },
    )

    k2 = CompactionEntry(
        session_id="demo",
        parent_id=d.id,
        source_leaf_id=d.id,
        first_kept_entry_id=d.id,
        summary=summary,
    )

    repeated = build_request_messages([
        *branch, d, k2,
    ])

    assert repeated[:2] == [a.payload, c.payload]
    assert repeated.count(a.payload) == 1
    assert repeated.count(c.payload) == 1
    assert repeated[-1] == d.payload
    print("连续压缩仍保留初始要求和后续补充：通过")

    other = MessageEntry(
        session_id="demo",
        parent_id=b.id,
        payload={
            "role": "user",
            "content": "改为只解释代码。",
        },
    )

    other_request = build_request_messages([a, b, other])

    assert other_request == [a.payload, b.payload, other.payload]
    assert c.payload not in other_request
    print("只保留当前分支的要求，不混入其他分支：通过")


if __name__ == "__main__":
    main()