import json

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunking import select_summary_chunk
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer


class SizeModel:
    max_output_tokens = 100

    def estimate_input_tokens(self, messages, tools):
        assert messages[0]["role"] == "system"
        assert tools[0]["name"] == "submit_context_summary"

        data = json.loads(messages[1]["content"])

        # 假计数：固定开销 100，每条历史消息 100。
        return 100 + 100 * len(data["messages"])

    def generate(self, messages, tools):
        raise AssertionError("选择分块时不能调用模型生成")


def main():
    entries = []

    def add(payload):
        entry = MessageEntry(
            session_id="demo",
            parent_id=entries[-1].id if entries else None,
            payload=payload,
        )
        entries.append(entry)
        return entry

    user = add({
        "role": "user",
        "content": "读取项目说明。",
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
        "content": "项目说明。",
        "is_error": False,
    })

    final = add({
        "role": "assistant",
        "type": "final",
        "content": "已读取。",
    })

    kept = add({
        "role": "user",
        "content": "继续。",
    })

    plan = CompactionPlan(
        source_leaf_id=kept.id,
        to_summarize=[user, call, result, final],
        to_keep=[kept],
    )

    def make_summarizer(input_budget):
        return ContextSummarizer(
            SizeModel(),
            context_budget=ContextBudget(
                context_window=input_budget + 200,
                max_output_tokens=100,
                safety_margin=100,
            ),
        )

    # 用户消息需要 200；再加完整工具组需要 400。
    chunk = select_summary_chunk(plan, make_summarizer(300))
    assert chunk == [user]
    print("放不下完整工具组时，不只保留工具调用：通过")

    chunk = select_summary_chunk(plan, make_summarizer(400))
    assert chunk == [user, call, result]
    print("预算允许时，调用和结果一起进入分块：通过")

    chunk = select_summary_chunk(plan, make_summarizer(500))
    assert chunk == plan.to_summarize
    assert plan.to_keep == [kept]
    assert len(plan.to_summarize) == 4
    print("全部放得下时保留全部，且不修改原计划：通过")

    try:
        select_summary_chunk(plan, make_summarizer(199))
    except ModelError as exc:
        assert exc.code == "summary_group_too_large"
    else:
        raise AssertionError("第一组放不下时应报错")

    print("第一组超预算时明确停止：通过")


if __name__ == "__main__":
    main()