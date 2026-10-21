from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.budget.summary_job import SummaryJobLimits

from tests.context.check_chunked_summarizer import MergeModel
from tests.context.check_session_compactor import make_store


def main():
    budget = ContextBudget(
        context_window=450,
        max_output_tokens=100,
        safety_margin=100,
    )

    # 此测试历史分两块：
    # 第一块预留 200 + 100 = 300
    # 第二块预留 250 + 100 = 350
    # 总计 650
    cases = [
        ("success", 8, 650, None),
        ("tokens", 8, 600, "summary_job_token_limit"),
        ("requests", 1, 10000, "summary_job_request_limit"),
    ]

    with TemporaryDirectory() as directory:
        for name, max_requests, max_tokens, expected_error in cases:
            store = make_store(directory, f"{name}.jsonl")
            before = store.path.read_bytes()
            model = MergeModel()
            stats = ContextStats()

            chunked = ChunkedSummarizer(
                ContextSummarizer(
                    model,
                    context_budget=budget,
                    stats=stats,
                ),
                job_limits=SummaryJobLimits(
                    max_requests=max_requests,
                    max_reserved_tokens=max_tokens,
                ),
            )

            compactor = SessionCompactor(store, chunked)

            if expected_error is None:
                entry = compactor.compact(keep_recent_groups=1)

                assert entry is not None
                assert model.calls == 2
                assert stats.summary_requests == 2
                assert chunked.last_job_budget.requests == 2
                assert chunked.last_job_budget.reserved_tokens == 650
                print("共享预算累计两块，恰好达到上限可以发布：通过")
                continue

            previous_job = None

            # 再次发起作业，验证不会沿用上次已消耗的预算。
            for attempt in range(2):
                try:
                    compactor.compact(keep_recent_groups=1)
                except ModelError as exc:
                    assert exc.code == expected_error
                else:
                    raise AssertionError("应在第二块请求前停止")

                job = chunked.last_job_budget

                assert job is not previous_job
                assert job.requests == 1
                assert job.reserved_tokens == 300
                assert model.calls == attempt + 1
                assert stats.summary_requests == attempt + 1
                assert store.path.read_bytes() == before

                previous_job = job

            print(
                f"{name}：第二块不发送，不发布部分摘要，"
                "新作业预算独立：通过"
            )


if __name__ == "__main__":
    main()