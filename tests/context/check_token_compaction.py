from forgepilot.exceptions import ModelError
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.compaction.token_planning import (
    plan_compaction_by_tokens,
)


class WeightedModel:
    def __init__(self):
        self.requests = []

    def estimate_input_tokens(self, messages, tools):
        self.requests.append({
            "messages": messages,
            "tools": tools,
        })

        # 假计数：包装固定 100，每个 assistant/tool 消息 100。
        history_cost = sum(
            100
            for message in messages
            if message["role"] in {"assistant", "tool"}
        )

        # 原始要求过大时，也必须参与预算。
        requirement_cost = sum(
            1000
            for message in messages
            if message.get("content") == "超大的原始要求"
        )

        return 100 + history_cost + requirement_cost

    def generate(self, messages, tools):
        raise AssertionError("规划边界不能请求模型")


def main():
    entries = []

    def add(payload):
        node = MessageEntry(
            session_id="demo",
            parent_id=entries[-1].id if entries else None,
            payload=payload,
        )
        entries.append(node)
        return node

    user = add({"role": "user", "content": "检查代码。"})
    old = add({
        "role": "assistant",
        "type": "final",
        "content": "准备读取。",
    })
    call = add({
        "role": "assistant",
        "type": "tool_call",
        "id": "call_read",
        "name": "read_file",
        "arguments": {"path": "README.md"},
    })
    result = add({
        "role": "tool",
        "tool_call_id": "call_read",
        "content": "文件内容。",
        "is_error": False,
    })
    latest = add({
        "role": "assistant",
        "type": "final",
        "content": "已读取。",
    })

    before = [node.model_dump() for node in entries]
    model = WeightedModel()

    plan = plan_compaction_by_tokens(
        entries,
        model=model,
        tools=[],
        target_input_tokens=500,
        summary_reserve_tokens=100,
    )

    # 保留三个组需要 600，保留两个组需要 500。
    assert plan is not None
    assert plan.to_summarize == [user, old]
    assert plan.to_keep == [call, result, latest]
    print("预算允许时尽量保留更多近期完整组：通过")

    smaller = plan_compaction_by_tokens(
        entries,
        model=model,
        tools=[],
        target_input_tokens=300,
        summary_reserve_tokens=100,
    )

    assert smaller.to_keep == [latest]
    assert call in smaller.to_summarize
    assert result in smaller.to_summarize
    print("缩小预算时整组移动，不拆开调用与结果：通过")

    assert [node.model_dump() for node in entries] == before
    print("规划不修改原始历史：通过")

    user.payload["content"] = "超大的原始要求"

    try:
        plan_compaction_by_tokens(
            entries,
            model=model,
            tools=[],
            target_input_tokens=500,
            summary_reserve_tokens=100,
        )
    except ModelError as exc:
        assert exc.code == "retained_context_too_large"
    else:
        raise AssertionError("原始要求超预算时应明确停止")

    print("压缩区域中的原始要求仍计入预算：通过")


if __name__ == "__main__":
    main()