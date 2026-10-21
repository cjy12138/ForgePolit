from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.budget.summary_job import (
    SummaryJobBudget,
    SummaryJobLimits,
)

from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


class ScriptedSummaryModel(FakeSummaryModel):
    max_output_tokens = 100

    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = iter(outcomes)
        self.attempts = 0
        self.prompts = []

    def estimate_input_tokens(self, messages, tools):
        corrected = "上一次响应未通过" in messages[0]["content"]
        return 250 if corrected else 200

    def generate(self, messages, tools):
        self.attempts += 1
        self.prompts.append(messages[0]["content"])
        outcome = next(self.outcomes)

        if outcome == "invalid":
            return {"type": "final", "content": "未按工具格式提交"}

        if outcome == "ok":
            return super().generate(messages, tools)

        raise ModelError(outcome, "模拟请求错误")


def main():
    cases = [
        ("corrected", ["invalid", "ok"], 10000, None, 2),
        (
            "still_invalid",
            ["invalid", "invalid"],
            10000,
            "invalid_context_summary",
            2,
        ),
        (
            "network_then_invalid",
            ["timeout", "invalid"],
            10000,
            "invalid_context_summary",
            2,
        ),
        (
            "invalid_then_network",
            ["invalid", "timeout"],
            10000,
            "timeout",
            2,
        ),
        (
            "authentication",
            ["authentication_error"],
            10000,
            "authentication_error",
            1,
        ),
        (
            "budget",
            ["invalid", "ok"],
            500,
            "summary_job_token_limit",
            1,
        ),
    ]

    with TemporaryDirectory() as directory:
        for name, outcomes, token_limit, expected_error, attempts in cases:
            store = make_store(directory, f"{name}.jsonl")
            before = store.path.read_bytes()

            plan = plan_compaction(
                store.get_current_branch(),
                keep_recent_groups=1,
            )
            assert plan is not None

            model = ScriptedSummaryModel(outcomes)
            stats = ContextStats()
            job = SummaryJobBudget(SummaryJobLimits(
                max_requests=8,
                max_reserved_tokens=token_limit,
            ))

            summarizer = ContextSummarizer(
                model,
                context_budget=ContextBudget(
                    context_window=1000,
                    max_output_tokens=100,
                    safety_margin=100,
                ),
                stats=stats,
            )

            try:
                result = summarizer.summarize_with_sources(
                    plan,
                    job_budget=job,
                )
            except ModelError as exc:
                assert expected_error is not None
                assert exc.code == expected_error
            else:
                assert expected_error is None
                assert result.summary.goal

            assert model.attempts == attempts
            assert stats.summary_requests == attempts
            assert job.requests == attempts
            assert store.path.read_bytes() == before

            if outcomes[0] == "invalid" and attempts == 2:
                assert "上一次响应未通过" in model.prompts[1]
                assert job.reserved_tokens == 650

            print(f"{name}：恢复共用两次上限和作业预算：通过")


if __name__ == "__main__":
    main()