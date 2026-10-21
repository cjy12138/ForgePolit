from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


class NoTools:
    def definitions(self):
        return []

    def execute(self, *args, **kwargs):
        raise AssertionError("本测试不应执行工具")


class RecordingModel:
    max_output_tokens = 100

    def __init__(self, estimated_tokens):
        self.estimated_tokens = estimated_tokens
        self.estimated_requests = []
        self.sent_requests = []

    def estimate_input_tokens(self, messages, tools):
        self.estimated_requests.append(deepcopy({
            "messages": messages,
            "tools": tools,
        }))
        return self.estimated_tokens

    def generate(self, messages, tools):
        self.sent_requests.append(deepcopy({
            "messages": messages,
            "tools": tools,
        }))
        return {
            "type": "final",
            "content": "已保留要求并继续处理。",
        }


def main():
    budget = ContextBudget(
        context_window=1000,
        max_output_tokens=100,
        safety_margin=100,
    )
    assert budget.input_budget == 800

    with TemporaryDirectory() as directory:
        for action in ("run", "resume"):
            store = make_store(directory, f"{action}.jsonl")
            original_request = deepcopy(
                store.get_current_branch()[0].payload
            )

            SessionCompactor(
                store,
                ContextSummarizer(FakeSummaryModel()),
            ).compact(keep_recent_groups=1)

            model = RecordingModel(estimated_tokens=200)
            agent = Agent(
                model,
                NoTools(),
                ToolContext(Path(directory)),
                session_store=store,
                context_budget=budget,
            )

            if action == "run":
                result = agent.run("继续。")
            else:
                result = agent.resume()

            assert result["status"] == "completed"
            assert result["n_calls"] == 1
            assert model.estimated_requests == model.sent_requests

            sent_messages = model.sent_requests[0]["messages"]
            assert original_request in sent_messages
            assert sent_messages.count(original_request) == 1

            print(f"{action} 保留原始要求，估算与发送一致：通过")

        # 超过预算时，不发送请求，也不追加模型响应。
        store = make_store(directory, "over-budget.jsonl")
        before = store.path.read_bytes()

        model = RecordingModel(estimated_tokens=801)
        agent = Agent(
            model,
            NoTools(),
            ToolContext(Path(directory)),
            session_store=store,
            context_budget=budget,
        )

        result = agent.resume()

        assert result["status"] == "failed"
        assert result["error_code"] == "context_budget_exceeded"
        assert result["n_calls"] == 0
        assert model.sent_requests == []
        assert store.path.read_bytes() == before
        print("超预算时不发送、不计请求次数、不修改会话：通过")

        # 输出配置不一致必须立即报错。
        model.max_output_tokens = 200

        try:
            Agent(
                model,
                NoTools(),
                ToolContext(Path(directory)),
                context_budget=budget,
            )
        except ValueError as exc:
            assert "输出上限" in str(exc)
        else:
            raise AssertionError("应拒绝不一致的输出配置")

        print("拒绝模型与预算配置不一致：通过")


if __name__ == "__main__":
    main()