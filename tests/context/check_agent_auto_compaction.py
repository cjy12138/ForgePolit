from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_agent_context_budget import (
    NoTools,
    RecordingModel,
)
from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


class SequenceEstimateModel(RecordingModel):
    def __init__(self, estimates):
        super().__init__(estimated_tokens=0)
        self.estimates = iter(estimates)

    def estimate_input_tokens(self, messages, tools):
        self.estimated_tokens = next(self.estimates)
        return super().estimate_input_tokens(messages, tools)


def main():
    budget = ContextBudget(
        context_window=1000,
        max_output_tokens=100,
        safety_margin=100,
        compact_ratio=0.9,
    )

    # 输入预算 800，压缩触发点 720。
    cases = [
        ("low", [200], False, "completed", 0, 1),
        ("compact", [750, 200], False, "completed", 1, 1),
        ("still_over", [900, 850], False, "failed", 1, 0),
        ("summary_failed", [750], True, "failed", 0, 0),
    ]

    with TemporaryDirectory() as directory:
        for (
            name,
            estimates,
            fail_summary,
            expected_status,
            expected_compactions,
            expected_requests,
        ) in cases:
            store = make_store(directory, f"{name}.jsonl")
            original = store.get_current_branch()[0].payload
            before = store.path.read_bytes()

            model = SequenceEstimateModel(estimates)
            summary_model = FakeSummaryModel(fail=fail_summary)

            compactor = SessionCompactor(
                store,
                ContextSummarizer(summary_model),
            )

            agent = Agent(
                model,
                NoTools(),
                ToolContext(Path(directory)),
                session_store=store,
                context_budget=budget,
                compactor=compactor,
                keep_recent_groups=1,
            )

            result = agent.resume()

            assert result["status"] == expected_status
            assert result["n_calls"] == expected_requests
            assert len(model.sent_requests) == expected_requests

            branch = store.get_current_branch()
            compactions = [
                node
                for node in branch
                if isinstance(node, CompactionEntry)
            ]
            assert len(compactions) == expected_compactions

            expected_summary_calls = 0 if name == "low" else 1
            assert summary_model.calls == expected_summary_calls

            if model.sent_requests:
                sent = model.sent_requests[0]
                assert original in sent["messages"]

                # 实际发送的内容必须是最后一次估算的内容。
                assert sent == model.estimated_requests[-1]

            if name == "compact":
                assert len(model.estimated_requests) == 2
                assert (
                    model.estimated_requests[0]["messages"]
                    != model.sent_requests[0]["messages"]
                )

            if name == "still_over":
                assert (
                    result["error_code"]
                    == "context_budget_exceeded"
                )

            if name == "summary_failed":
                assert result["error_code"] == "fake_failure"
                assert store.path.read_bytes() == before

            print(f"{name}：通过")


if __name__ == "__main__":
    main()