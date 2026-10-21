from forgepilot.harness import Harness
from forgepilot.tasks import TaskIntent, TaskRequest


class NeedsInputInterpreter:
    def interpret(self, request):
        return TaskIntent(
            goal="修复用户提到的问题",
            suggested_mode="bugfix",
            clarification_question="具体是哪一个错误？",
        )


class MustNotRunAgent:
    def run(self, prompt):
        raise AssertionError("需要澄清时不应该启动 Agent")

class ClarifyingInterpreter:
    def __init__(self):
        self.requests = []

    def interpret(self, request):
        self.requests.append(request)

        if len(self.requests) == 1:
            return TaskIntent(
                goal="修复加法函数",
                suggested_mode="bugfix",
                clarification_question="add 函数应该返回什么？",
            )

        return TaskIntent(
            goal="让 add 返回两个参数的和",
            suggested_mode="bugfix",
            clarification_question="",
        )


class RecordingAgent:
    def __init__(self):
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        return {
            "status": "completed",
            "answer": "模拟执行完成",
            "messages": [],
            "n_calls": 0,
        }

def main():
    harness = Harness(
        interpreter=NeedsInputInterpreter(),
        agent=MustNotRunAgent(),
    )

    result = harness.run(
        TaskRequest(text="修复那个问题")
    )

    assert result["status"] == "needs_input"
    assert result["question"] == "具体是哪一个错误？"
    print("澄清时不启动 Agent：通过")

    interpreter = ClarifyingInterpreter()
    agent = RecordingAgent()

    prepared_tasks = []

    def prepare_coding(task_data):
        # 准备发生在 Agent 执行之前。
        assert agent.prompts == []
        prepared_tasks.append(task_data.copy())

    harness = Harness(
        interpreter,
        agent,
        coding_prepare=prepare_coding,
    )

    first = harness.run(
        TaskRequest(
            text="修复加法函数，不修改测试文件。",
            mode="coding",
        )
    )

    assert first["status"] == "needs_input"
    assert agent.prompts == []

    second = harness.run(
        TaskRequest(
            text=(
                "原始需求：修复加法函数，不修改测试文件。\n"
                "澄清问题：add 函数应该返回什么？\n"
                "用户回答：返回两个参数的和。"
            ),
            mode="coding",
        )
    )

    assert second["status"] == "completed"
    assert len(agent.prompts) == 1
    assert "不修改测试文件" in agent.prompts[0]
    assert "返回两个参数的和" in agent.prompts[0]
    assert second["mode"] == "coding"

    print("澄清后执行一次，保留约束和指定模式：通过")


if __name__ == "__main__":
    main()