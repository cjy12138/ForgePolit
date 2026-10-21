from pathlib import Path

from forgepilot.permissions.models import ToolPermissionRequest
from forgepilot.permissions.reviewer import SafetyReviewer
from forgepilot.sessions.budget.context_budget import ContextBudget


class FakeReviewModel:
    max_output_tokens = 512

    def __init__(self, verdict):
        self.verdict = verdict

    def estimate_input_tokens(self, *, messages, tools):
        return 500

    def generate(self, *, messages, tools):
        if self.verdict == "error":
            raise TimeoutError("模拟超时")

        return {
            "type": "tool_call",
            "id": "review_1",
            "name": "submit_safety_review",
            "arguments": {
                "verdict": self.verdict,
                "reason": "模拟审查意见",
            },
        }


def main():
    request = ToolPermissionRequest(
        tool_name="run_command",
        arguments={
            "command": "python -m unittest discover -v",
        },
        workspace=str(Path.cwd()),
        mode="auto",
        task="运行已有测试，检查当前代码。",
    )

    budget = ContextBudget(
        context_window=8192,
        max_output_tokens=512,
        safety_margin=512,
    )

    for verdict in [
        "allow",
        "deny",
        "uncertain",
        "invalid",
        "error",
    ]:
        reviewer = SafetyReviewer(
            FakeReviewModel(verdict),
            context_budget=budget,
        )

        result = reviewer.review(request)
        print(f"{verdict} → {result.action}：{result.reason}")


if __name__ == "__main__":
    main()