import json

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import MessageEntry
from forgepilot.skills.extractor import SkillExtractor


class FakeExtractionModel:
    max_output_tokens = 2048

    def __init__(self):
        self.calls = 0
        self.estimated_tokens = 100
        self.candidate = None
        self.request = None

    def estimate_input_tokens(self, *, messages, tools):
        return self.estimated_tokens

    def generate(self, *, messages, tools):
        assert [tool["name"] for tool in tools] == [
            "submit_skill_candidate"
        ]

        self.calls += 1
        self.request = json.loads(messages[1]["content"])

        return {
            "type": "tool_call",
            "id": f"extract_{self.calls}",
            "name": "submit_skill_candidate",
            "arguments": {
                "candidate": self.candidate,
                "reason": "脚本模型返回预设结果。",
            },
        }


def main():
    user = MessageEntry(
        session_id="test-session",
        payload={
            "role": "user",
            "content": "解释 Python 函数的行为。",
        },
    )

    final = MessageEntry(
        session_id="test-session",
        parent_id=user.id,
        payload={
            "role": "assistant",
            "type": "final",
            "content": "已完成静态分析，没有运行测试。",
        },
    )

    entries = [user, final]

    model = FakeExtractionModel()

    extractor = SkillExtractor(
        model=model,
        context_budget=ContextBudget(
            context_window=32000,
            max_output_tokens=2048,
            safety_margin=2000,
        ),
    )

    allowed_tools = {"read_file", "list_files"}

    # 1. 合法候选通过校验。
    model.candidate = {
        "metadata": {
            "name": "inspect_python",
            "description": "静态阅读 Python 代码并解释行为。",
            "tools": ["read_file"],
        },
        "content": (
            "## 适用条件\n需要静态分析 Python 代码。\n"
            "## 操作步骤\n读取相关文件并分析函数行为。\n"
            "## 验证方法\n说明静态分析的依据及未运行的测试。"
        ),
    }

    result = extractor.extract(
        entries=entries,
        allowed_tools=allowed_tools,
    )

    assert result.candidate is not None
    assert result.candidate.metadata.name == "inspect_python"

    assert model.request["allowed_tools"] == [
        "list_files",
        "read_file",
    ]

    assert [
        item["id"]
        for item in model.request["entries"]
    ] == [user.id, final.id]

    print("合法候选通过，输入包含来源记录和工具范围：通过")

    # 2. 没有值得提取的流程，可以返回空候选。
    model.candidate = None

    result = extractor.extract(
        entries=entries,
        allowed_tools=allowed_tools,
    )

    assert result.candidate is None

    print("允许明确不提取 Skill：通过")

    # 3. 模型不能声明当前任务不支持的工具。
    model.candidate = {
        "metadata": {
            "name": "shell_workflow",
            "description": "执行 shell 命令。",
            "tools": ["run_command"],
        },
        "content": "运行命令后检查实际结果。",
    }

    try:
        extractor.extract(
            entries=entries,
            allowed_tools=allowed_tools,
        )
    except ValueError as exc:
        assert "run_command" in str(exc)
    else:
        raise AssertionError("非法工具依赖未被拒绝")

    print("拒绝当前任务不支持的工具：通过")

    # 4. 未完成任务在请求模型之前被拒绝。
    calls_before = model.calls

    try:
        extractor.extract(
            entries=[user],
            allowed_tools=allowed_tools,
        )
    except ValueError as exc:
        assert "最终回答" in str(exc)
    else:
        raise AssertionError("未完成任务仍进行了提取")

    assert model.calls == calls_before

    print("未完成任务不请求提取模型：通过")

    # 5. 输入超预算时，不请求模型。
    model.estimated_tokens = 100000

    try:
        extractor.extract(
            entries=entries,
            allowed_tools=allowed_tools,
        )
    except ModelError as exc:
        assert exc.code == "skill_extraction_budget_exceeded"
    else:
        raise AssertionError("超预算请求未被拒绝")

    assert model.calls == calls_before

    print("输入超预算不请求提取模型：通过")


if __name__ == "__main__":
    main()