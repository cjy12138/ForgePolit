import json

from forgepilot.skills.models import SkillMetadata
from forgepilot.skills.selector import SkillSelector
from forgepilot.sessions.budget.context_budget import ContextBudget


class FakeSelectionModel:
    max_output_tokens = 512

    def __init__(self, selected_name: str | None):
        self.selected_name = selected_name
        self.calls = 0
        self.request = None

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1
        self.request = json.loads(
            messages[1]["content"]
        )

        assert [tool["name"] for tool in tools] == [
            "submit_skill_choice"
        ]

        return {
            "type": "tool_call",
            "id": f"selection_{self.calls}",
            "name": "submit_skill_choice",
            "arguments": {
                "name": self.selected_name,
                "reason": "脚本模型返回预设结果。",
            },
        }