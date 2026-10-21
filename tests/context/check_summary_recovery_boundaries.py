from tempfile import TemporaryDirectory
from threading import Event

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.budget.summary_job import (
    SummaryJobBudget,
    SummaryJobLimits,
)

from tests.context.check_session_compactor import make_store
from tests.context.check_summary_correction import ScriptedSummaryModel
from tests.context.check_summary_job_timeout import FakeClock


class InterruptedCorrectionModel(ScriptedSummaryModel):
    def __init__(self, mode, event, clock):
        super().__init__(["invalid", "ok"])
        self.mode = mode
        self.event = event
        self.clock = clock

    def estimate_input_tokens(self, messages, tools):
        corrected = "上一次响应未通过" in messages[0]["content"]

        if self.mode == "input_limit" and corrected:
            return 900

        return super().estimate_input_tokens(messages, tools)

    def generate(self, messages, tools):
        response = super().generate(messages, tools)

        if self.attempts == 1:
            if self.mode == "cancel":
                self.event.set()

            if self.mode == "deadline":
                self.clock.advance(6)

        return response


def main():
    cases = [
        ("input_limit", "summary_budget_exceeded"),
        ("request_limit", "summary_job_request_limit"),
        ("cancel", "summary_job_cancelled"),
        ("deadline", "summary_job_timeout"),
    ]

    with TemporaryDirectory() as directory:
        for mode, expected_code in cases:
            store = make_store(directory, f"{mode}.jsonl")
            before = store.path.read_bytes()

            plan = plan_compaction(
                store.get_current_branch(),
                keep_recent_groups=1,
            )
            assert plan is not None

            event = Event()
            clock = FakeClock()
            model = InterruptedCorrectionModel(mode, event, clock)
            stats = ContextStats()

            job = SummaryJobBudget(
                SummaryJobLimits(
                    max_requests=1 if mode == "request_limit" else 8,
                    max_reserved_tokens=10000,
                    max_elapsed_seconds=5,
                ),
                clock=clock,
                is_cancelled=event.is_set,
            )

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
                summarizer.summarize_with_sources(
                    plan,
                    job_budget=job,
                )
            except ModelError as exc:
                assert exc.code == expected_code
            else:
                raise AssertionError("不应发送并完成纠正请求")

            assert model.attempts == 1
            assert stats.summary_requests == 1
            assert job.requests == 1
            assert job.reserved_tokens == 300
            assert store.path.read_bytes() == before

            print(f"{mode}：停止纠正，不发送第二次请求：通过")


if __name__ == "__main__":
    main()