from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.benefit import CompactionBenefitCheck
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_agent_auto_compaction import SequenceEstimateModel
from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


def main():
    cases = [
        ("useful", [750, 500], None),
        ("too_small", [750, 740], "compaction_insufficient_savings"),
        ("larger", [750, 790], "compaction_insufficient_savings"),
        ("over", [900, 850], "compaction_candidate_over_budget"),
    ]

    with TemporaryDirectory() as directory:
        for name, estimates, expected_error in cases:
            store = make_store(directory, f"{name}.jsonl")
            before = store.path.read_bytes()

            model = SequenceEstimateModel(estimates)
            summary_model = FakeSummaryModel()

            compactor = SessionCompactor(
                store,
                ContextSummarizer(summary_model),
                publication_check=CompactionBenefitCheck(
                    model=model,
                    tools=[],
                    max_input_tokens=800,
                    minimum_savings_tokens=100,
                ),
            )

            if expected_error is None:
                entry = compactor.compact(keep_recent_groups=1)

                assert entry is not None
                assert store.path.read_bytes().startswith(before)
                assert store.get_current_branch()[-1].id == entry.id
            else:
                try:
                    compactor.compact(keep_recent_groups=1)
                except ModelError as exc:
                    assert exc.code == expected_error
                else:
                    raise AssertionError("不应发布这个候选摘要")

                assert store.path.read_bytes() == before

            assert summary_model.calls == 1
            assert model.sent_requests == []
            print(f"{name}：通过")


if __name__ == "__main__":
    main()