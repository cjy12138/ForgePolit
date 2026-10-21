from dataclasses import replace
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


class RejectOnce:
    def __init__(self):
        self.calls = 0

    def __call__(self, branch, entry):
        self.calls += 1

        if self.calls == 1:
            raise ModelError(
                "test_publication_rejected",
                "模拟第一次发布检查拒绝。",
            )


def main():
    with TemporaryDirectory() as directory:
        store = make_store(directory, "cache.jsonl")
        before = store.path.read_bytes()

        cache = SummaryCandidateCache(profile={
            "model": "fake",
            "prompt_version": 1,
        })
        model = FakeSummaryModel()
        check = RejectOnce()

        compactor = SessionCompactor(
            store,
            ContextSummarizer(model),
            publication_check=check,
            candidate_cache=cache,
        )

        try:
            compactor.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "test_publication_rejected"
        else:
            raise AssertionError("第一次应拒绝发布")

        assert model.calls == 1
        assert store.path.read_bytes() == before

        plan = plan_compaction(
            store.get_current_branch(),
            keep_recent_groups=1,
        )
        assert plan is not None
        assert cache.get(plan) is not None
        print("生成成功但发布被拒绝时保留候选：通过")

        # 取出的摘要是副本。
        copy = cache.get(plan)
        copy.goal.clear()
        assert cache.get(plan).goal
        print("调用方不能修改缓存原件：通过")

        # 内容改变，即使节点 ID 保持不变，也不命中。
        changed = plan.to_summarize[0].model_copy(deep=True)
        changed.payload["content"] = "改变后的要求"

        changed_plan = replace(
            plan,
            to_summarize=[changed, *plan.to_summarize[1:]],
        )
        assert cache.get(changed_plan) is None
        print("来源内容变化后不复用旧候选：通过")

        entry = compactor.compact(keep_recent_groups=1)

        assert entry is not None
        assert model.calls == 1
        assert check.calls == 2
        assert store.get_current_branch()[-1].id == entry.id
        print("复用候选不再请求摘要，但重新执行发布检查：通过")


if __name__ == "__main__":
    main()