import json
from copy import deepcopy

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.schema import ContextSummary


def make_summary(text, ids):
    return ContextSummary(
        goal=[{
            "text": text,
            "source_entry_ids": ids,
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


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def generate(self, messages, tools):
        self.requests.append(deepcopy({
            "messages": messages,
            "tools": tools,
        }))
        return deepcopy(self.response)


def main():
    new_entry = MessageEntry(
        session_id="demo",
        payload={
            "role": "user",
            "content": "继续解释会话模块。",
        },
    )
    kept_entry = MessageEntry(
        session_id="demo",
        parent_id=new_entry.id,
        payload={
            "role": "assistant",
            "type": "final",
            "content": "会话采用树形结构。",
        },
    )

    previous = make_summary(
        "用户希望了解项目",
        ["old-source"],
    )

    plan = CompactionPlan(
        source_leaf_id=kept_entry.id,
        to_summarize=[new_entry],
        to_keep=[kept_entry],
        previous_summary=previous,
        previous_compaction_id="old-compaction",
    )

    expected = make_summary(
        "用户希望了解项目，并进一步了解会话模块",
        ["old-source", new_entry.id],
    )

    response = {
        "type": "tool_call",
        "id": "summary-submit",
        "name": "submit_context_summary",
        "arguments": expected.model_dump(),
    }

    model = FakeModel(response)
    result = ContextSummarizer(model).summarize(plan)

    assert result == expected
    assert len(model.requests) == 1

    request = model.requests[0]
    assert [tool["name"] for tool in request["tools"]] == [
        "submit_context_summary"
    ]

    data = json.loads(request["messages"][1]["content"])

    assert data["previous_summary"] == previous.model_dump()
    assert data["messages"] == [{
        "id": new_entry.id,
        "message": new_entry.payload,
    }]

    print("只发送旧摘要和待压缩消息，合法的新旧来源均可保留：通过")

    invalid_source = deepcopy(response)
    invalid_source["arguments"]["goal"][0][
        "source_entry_ids"
    ] = [kept_entry.id]

    wrong_tool = deepcopy(response)
    wrong_tool["name"] = "run_python"

    incomplete = deepcopy(response)
    del incomplete["arguments"]["constraints"]

    invalid_responses = [
        invalid_source,
        wrong_tool,
        incomplete,
        {"type": "final", "content": "普通文本摘要"},
        {"type": "tool_batch", "calls": [response]},
    ]

    for invalid in invalid_responses:
        bad_model = FakeModel(invalid)

        try:
            ContextSummarizer(bad_model).summarize(plan)
        except ModelError as exc:
            assert exc.code == "invalid_context_summary"
        else:
            raise AssertionError("非法摘要响应不应通过")

        assert len(bad_model.requests) == 2

        first_request, correction_request = bad_model.requests

        assert (
                first_request["messages"][1]["content"]
                == correction_request["messages"][1]["content"]
        )
        assert (
                first_request["tools"]
                == correction_request["tools"]
        )
        assert (
                "上一次响应未通过"
                in correction_request["messages"][0]["content"]
        )

    print("拒绝范围外引用、业务工具、不完整结构及错误返回类型：通过")


if __name__ == "__main__":
    main()