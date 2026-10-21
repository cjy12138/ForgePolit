from copy import deepcopy
from tempfile import TemporaryDirectory

from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.cache.profile import build_summary_cache_profile
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_chunked_summarizer import MergeModel
from tests.context.check_session_compactor import make_store


def main():
    model = MergeModel()
    model.model_name = "fake-model"

    chunked = ChunkedSummarizer(
        ContextSummarizer(
            model,
            context_budget=ContextBudget(
                context_window=1000,
                max_output_tokens=100,
                safety_margin=100,
            ),
        ),
        max_chunks=8,
    )

    profile = build_summary_cache_profile(
        chunked,
        provider_id="fake-provider",
    )

    assert profile["model_name"] == "fake-model"
    assert profile["summary_budget"]["context_window"] == 1000
    assert "summary_prompt" in profile
    assert "summary_schema" in profile
    print("配置标识包含模型、规则、Schema 和预算：通过")

    with TemporaryDirectory() as directory:
        store = make_store(directory, "profile.jsonl")
        plan = plan_compaction(
            store.get_current_branch(),
            keep_recent_groups=1,
        )
        assert plan is not None

        original_key = SummaryCandidateCache(
            profile=profile
        ).make_key(plan)

        for field, value in (
            ("model_name", "another-model"),
            ("summary_prompt", "另一套摘要规则"),
            ("max_output_tokens", 200),
            ("max_chunks", 4),
        ):
            changed = deepcopy(profile)
            changed[field] = value

            changed_key = SummaryCandidateCache(
                profile=changed
            ).make_key(plan)

            assert changed_key != original_key

        print("模型、规则或输出配置变化后缓存键改变：通过")


if __name__ == "__main__":
    main()