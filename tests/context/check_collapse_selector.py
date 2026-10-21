import json

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.models import MessageEntry


class FakeSelectionModel:
    max_output_tokens = 512

    def __init__(self, mode):
        self.mode = mode
        self.requests = 0
        self.selected_start_id = None

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.requests += 1
        assert [tool["name"] for tool in tools] == [
            "submit_collapse_choice"
        ]

        data = json.loads(messages[1]["content"])

        if self.mode == "skip":
            arguments = {
                "action": "skip",
                "candidate_id": None,
                "reason": "没有足够信息判断稳定性。",
            }
        else:
            # 选择最后一个合法候选，验证并非只能选最早范围。
            chosen = data["candidates"][-1]
            self.selected_start_id = chosen["source_start_id"]

            arguments = {
                "action": "collapse",
                "candidate_id": (
                    "invented"
                    if self.mode == "invalid"
                    else chosen["candidate_id"]
                ),
                "reason": "选择一个适合折叠的候选。",
            }

        return {
            "type": "tool_call",
            "id": "selection_call",
            "name": "submit_collapse_choice",
            "arguments": arguments,
        }


def main():
    branch = []

    def append(payload):
        entry = MessageEntry(
            session_id="demo",
            parent_id=branch[-1].id if branch else None,
            payload=payload,
        )
        branch.append(entry)
        return entry

    append({"role": "user", "content": "检查项目。"})

    calls = []

    for index in range(6):
        call_id = f"call_{index}"

        calls.append(append({
            "role": "assistant",
            "type": "tool_call",
            "id": call_id,
            "name": "read_file",
            "arguments": {"path": "calculator.py"},
        }))
        append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"输出 {index}",
            "is_error": False,
            "exit_code": 0,
        })

    budget = ContextBudget(
        context_window=8000,
        max_output_tokens=512,
        safety_margin=500,
    )

    for mode in ("select", "skip", "invalid"):
        model = FakeSelectionModel(mode)
        selector = CollapseSelector(
            model,
            context_budget=budget,
        )

        try:
            plan = selector.select(
                branch,
                [],
                context_messages=[{
                    "role": "user",
                    "content": "当前正在处理另一个问题。",
                }],
            )
        except ModelError as exc:
            assert mode == "invalid"
            assert exc.code == "invalid_collapse_selection"
            print("拒绝模型虚构的候选：通过")
            continue

        assert model.requests == 1

        if mode == "skip":
            assert plan is None
            assert selector.last_choice.action == "skip"
            print("允许判断后不折叠：通过")
        else:
            assert plan is not None
            assert plan.source_start_id == model.selected_start_id
            assert plan.source_start_id != calls[0].id
            print("模型可以选择非最早的合法范围：通过")


if __name__ == "__main__":
    main()