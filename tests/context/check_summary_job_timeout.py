from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.budget.summary_job import (
    SummaryJobBudget,
    SummaryJobLimits,
)

from tests.context.check_chunked_summarizer import MergeModel
from tests.context.check_session_compactor import make_store


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TimedModel(MergeModel):
    def __init__(self, clock, durations):
        super().__init__()
        self.clock = clock
        self.durations = iter(durations)

    def generate(self, messages, tools):
        response = super().generate(messages, tools)
        self.clock.advance(next(self.durations))
        return response


def main():
    clock = FakeClock()
    job = SummaryJobBudget(
        SummaryJobLimits(
            max_requests=8,
            max_reserved_tokens=10000,
            max_elapsed_seconds=5,
        ),
        clock=clock,
    )

    clock.advance(5)

    try:
        job.reserve(
            estimated_input_tokens=200,
            max_output_tokens=100,
        )
    except ModelError as exc:
        assert exc.code == "summary_job_timeout"
    else:
        raise AssertionError("超时后不应允许预留")

    assert job.requests == 0
    assert job.reserved_tokens == 0
    print("请求前已超时，不扣额度：通过")

    cases = [
        ("success", [2, 2], 2, False),
        ("first_late", [6], 1, True),
        ("last_late", [2, 4], 2, True),
    ]

    with TemporaryDirectory() as directory:
        for name, durations, expected_calls, should_timeout in cases:
            store = make_store(directory, f"{name}.jsonl")
            before = store.path.read_bytes()
            clock = FakeClock()
            model = TimedModel(clock, durations)

            chunked = ChunkedSummarizer(
                ContextSummarizer(
                    model,
                    context_budget=ContextBudget(
                        context_window=450,
                        max_output_tokens=100,
                        safety_margin=100,
                    ),
                ),
                job_limits=SummaryJobLimits(
                    max_requests=8,
                    max_reserved_tokens=10000,
                    max_elapsed_seconds=5,
                ),
            )

            compactor = SessionCompactor(store, chunked)

            # 新预算对象创建时使用假时钟。
            with patch(
                "forgepilot.sessions.budget.summary_job.monotonic",
                new=clock,
            ):
                try:
                    entry = compactor.compact(
                        keep_recent_groups=1,
                    )
                except ModelError as exc:
                    assert should_timeout
                    assert exc.code == "summary_job_timeout"
                    assert store.path.read_bytes() == before
                else:
                    assert not should_timeout
                    assert entry is not None

            assert model.calls == expected_calls
            assert chunked.last_job_budget.requests == expected_calls
            print(f"{name}：请求返回后检查时间，超时不发布：通过")


if __name__ == "__main__":
    main()