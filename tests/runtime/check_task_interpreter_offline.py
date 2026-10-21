from copy import deepcopy

from forgepilot.exceptions import ModelError
from forgepilot.task_interpreter import TaskInterpreter
from forgepilot.tasks import TaskRequest


class ScriptedModel:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def generate(self, messages, tools):
        index = len(self.calls)

        # 保存调用时的快照，避免后续追加消息改变旧记录。
        self.calls.append({
            "messages": deepcopy(messages),
            "tools": deepcopy(tools),
        })

        if index >= len(self.responses):
            raise AssertionError("模型调用次数超过预期")

        response = self.responses[index]

        if isinstance(response, Exception):
            raise response

        return response


def valid_response():
    return {
        "type": "tool_call",
        "id": "intent_1",
        "name": "submit_task_intent",
        "arguments": {
            "goal": "修复加法函数",
            "suggested_mode": "bugfix",
            "clarification_question": "",
        },
    }


def main():
    request = TaskRequest(text="修复加法函数")

    # 1. 首次返回合法数据，不需要重试。
    model = ScriptedModel([valid_response()])
    intent = TaskInterpreter(model).interpret(request)

    assert intent.suggested_mode == "bugfix"
    assert len(model.calls) == 1
    print("首次成功，不重试：通过")

    # 2. 首次返回普通文本，第二次纠正成功。
    model = ScriptedModel([
        {
            "type": "final",
            "content": "我可以帮你修复。",
        },
        valid_response(),
    ])

    intent = TaskInterpreter(model).interpret(request)

    assert intent.goal == "修复加法函数"
    assert len(model.calls) == 2

    first_messages = model.calls[0]["messages"]
    second_messages = model.calls[1]["messages"]

    assert len(second_messages) == len(first_messages) + 1
    assert "上一轮输出不符合协议" in second_messages[-1]["content"]
    print("格式错误后纠正成功：通过")

    # 3. 工具调用外层合法，但内部模式非法。
    invalid = valid_response()
    invalid["arguments"]["suggested_mode"] = "debug"

    model = ScriptedModel([invalid, invalid])

    try:
        TaskInterpreter(model).interpret(request)
    except ModelError as exc:
        assert exc.code == "invalid_task_intent"
    else:
        raise AssertionError("连续非法输出应该失败")

    assert len(model.calls) == 2
    print("连续错误最多请求两次：通过")

    # 4. 认证失败应立即传给上层，不进行格式重试。
    model = ScriptedModel([
        ModelError("authentication_error", "模拟认证失败"),
    ])

    try:
        TaskInterpreter(model).interpret(request)
    except ModelError as exc:
        assert exc.code == "authentication_error"
    else:
        raise AssertionError("认证失败应该抛出错误")

    assert len(model.calls) == 1
    print("认证失败不重试：通过")


if __name__ == "__main__":
    main()