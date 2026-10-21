from copy import deepcopy
from types import SimpleNamespace

from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.budget.token_estimator import (
    estimate_request_tokens,
)


class FakeClient:
    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))

        return SimpleNamespace(choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    tool_calls=None,
                    content="完成",
                ),
            ),
        ])


def main():
    budget = ContextBudget(
        context_window=32000,
        max_output_tokens=2048,
        safety_margin=2000,
    )

    client = FakeClient()
    model = DeepSeekModel(
        client,
        max_output_tokens=budget.max_output_tokens,
    )

    messages = [{
        "role": "user",
        "content": "读取项目说明。",
    }]

    tools = [{
        "name": "read_file",
        "description": "读取工作区内的文本文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }]

    original = deepcopy(messages)

    estimated = model.estimate_input_tokens(messages, tools)

    # 估算只在本地运行，不能调用 API。
    assert client.requests == []
    assert messages == original

    result = model.generate(messages, tools)
    sent = client.requests[0]

    assert result["type"] == "final"
    assert estimated == estimate_request_tokens(sent)
    assert sent["max_tokens"] == budget.max_output_tokens
    print("估算使用实际请求格式，输出上限与预算一致：通过")

    without_tools = model.estimate_input_tokens(messages, [])
    assert estimated > without_tools
    print("工具定义计入输入估算：通过")

    longer = deepcopy(messages)
    longer[0]["content"] += "请保留原始约束。" * 100

    assert model.estimate_input_tokens(longer, tools) > estimated
    print("正文增长会增加输入估算：通过")

    interaction = [
        *messages,
        {
            "role": "assistant",
            "type": "tool_call",
            "id": "call_read",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        },
        {
            "role": "tool",
            "tool_call_id": "call_read",
            "content": "项目说明\n" * 100,
            "is_error": False,
            "exit_code": None,
        },
    ]

    assert (
        model.estimate_input_tokens(interaction, tools)
        > estimated
    )

    request = model.prepare_request(interaction, tools)
    assert (
        request["messages"][1]["tool_calls"][0]
        ["function"]["arguments"]
        == '{"path": "README.md"}'
    )
    print("工具调用参数和结果正文计入估算：通过")

    decision = budget.assess(estimated)
    assert not decision.exceeds_budget
    print("估算结果可交给预算模块判断：通过")


if __name__ == "__main__":
    main()