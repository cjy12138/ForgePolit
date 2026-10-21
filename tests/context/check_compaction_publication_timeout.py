from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.budget.summary_job import SummaryJobLimits

from tests.context.check_session_compactor import make_store
from tests.context.check_summary_job_timeout import FakeClock, TimedModel


def main():
    with TemporaryDirectory() as directory:
        store = make_store(directory, "session.jsonl")
        before = store.path.read_bytes()
        clock = FakeClock()

        # 两块生成各用一秒，共两秒。
        model = TimedModel(clock, [1, 1])
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

        cache = SummaryCandidateCache(
            profile={"test": "publication_timeout"},
        )
        publication_calls = 0

        def slow_once(branch, entry):
            nonlocal publication_calls
            publication_calls += 1

            if publication_calls == 1:
                clock.advance(6)

        compactor = SessionCompactor(
            store,
            chunked,
            candidate_cache=cache,
            publication_check=slow_once,
        )

        with patch(
            "forgepilot.sessions.budget.summary_job.monotonic",
            new=clock,
        ):
            try:
                compactor.compact(keep_recent_groups=1)
            except ModelError as exc:
                assert exc.code == "summary_job_timeout"
            else:
                raise AssertionError("发布检查后超时，不应写入")

            assert model.calls == 2
            assert store.path.read_bytes() == before

            plan = plan_compaction(
                store.get_current_branch(),
                keep_recent_groups=1,
            )
            assert plan is not None
            assert cache.get_result(plan) is not None
            print("发布阶段超时，不写会话，但保留合法候选：通过")

            # 同一压缩器再次调用，从缓存复用。
            # 不应沿用之前生成作业的过期时间。
            entry = compactor.compact(keep_recent_groups=1)

            assert entry is not None
            assert model.calls == 2
            assert publication_calls == 2
            assert store.get_current_branch()[-1].id == entry.id
            print("缓存复用不沿用旧作业超时，仍重新检查发布：通过")


if __name__ == "__main__":
    main()