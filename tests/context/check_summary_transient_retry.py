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


class TransientSummaryModel(FakeSummaryModel):
    max_output_tokens = 100

    def __init__(self, code, *, always_fail=False):
        super().__init__()
        self.code = code
        self.always_fail = always_fail
        self.attempts = 0

    def estimate_input_tokens(self, messages, tools):
        return 200

    def generate(self, messages, tools):
        self.attempts += 1

        if self.attempts == 1 or self.always_fail:
            raise ModelError(self.code, "模拟请求错误")

        return super().generate(messages, tools)


def main():
    cases = [
        # 名称、首次错误、一直失败、请求上限、Token上限、
        # 预期错误、实际请求数
        ("connection", "connection_error", False, 8, 10000, None, 2),
        ("timeout", "timeout", False, 8, 10000, None, 2),
        ("persistent", "timeout", True, 8, 10000, "timeout", 2),
        (
            "authentication",
            "authentication_error",
            False,
            8,
            10000,
            "authentication_error",
            1,
        ),
        (
            "request_limit",
            "timeout",
            False,
            1,
            10000,
            "summary_job_request_limit",
            1,
        ),
        (
            "token_limit",
            "timeout",
            False,
            8,
            500,
            "summary_job_token_limit",
            1,
        ),
    ]

    with TemporaryDirectory() as directory:
        for (
            name,
            code,
            always_fail,
            max_requests,
            max_tokens,
            expected_error,
            expected_attempts,
        ) in cases:
            store = make_store(directory, f"{name}.jsonl")
            before = store.path.read_bytes()

            plan = plan_compaction(
                store.get_current_branch(),
                keep_recent_groups=1,
            )
            assert plan is not None

            model = TransientSummaryModel(
                code,
                always_fail=always_fail,
            )
            stats = ContextStats()
            job = SummaryJobBudget(SummaryJobLimits(
                max_requests=max_requests,
                max_reserved_tokens=max_tokens,
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

            assert model.attempts == expected_attempts
            assert stats.summary_requests == expected_attempts
            assert job.requests == expected_attempts
            assert job.reserved_tokens == expected_attempts * 300
            assert store.path.read_bytes() == before

            print(f"{name}：重试有界，所有尝试共享预算：通过")


if __name__ == "__main__":
    main()