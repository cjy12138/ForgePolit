from functools import partial
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)
from tests.context.check_token_compaction import WeightedModel


def main():
    with TemporaryDirectory() as directory:
        store = make_store(directory, "success.jsonl")
        original_branch = store.get_current_branch()
        before = store.path.read_bytes()

        planning_model = WeightedModel()
        summary_model = FakeSummaryModel()

        builder = partial(
            __import__(
                "forgepilot.sessions.compaction.token_planning",
                fromlist=["plan_compaction_by_tokens"],
            ).plan_compaction_by_tokens,
            model=planning_model,
            tools=[],
            target_input_tokens=200,
            summary_reserve_tokens=100,
        )

        compactor = SessionCompactor(
            store,
            ContextSummarizer(summary_model),
            plan_builder=builder,
        )

        # 这个数量不会影响已注入的 Token 规划策略。
        entry = compactor.compact(keep_recent_groups=999)

        assert isinstance(entry, CompactionEntry)
        assert entry.first_kept_entry_id == original_branch[-1].id
        assert summary_model.calls == 1
        assert store.path.read_bytes().startswith(before)
        print("Token 计划决定边界，并成功保存节点：通过")

        # 刚完成压缩，没有新增消息，不立即再次压缩。
        assert compactor.compact(keep_recent_groups=999) is None
        assert summary_model.calls == 1
        print("没有新增历史时不重复生成摘要：通过")

        failed_store = make_store(directory, "failed.jsonl")
        before = failed_store.path.read_bytes()
        failed_summary_model = FakeSummaryModel()

        failed_builder = partial(
            builder.func,
            model=planning_model,
            tools=[],
            target_input_tokens=199,
            summary_reserve_tokens=100,
        )

        failed_compactor = SessionCompactor(
            failed_store,
            ContextSummarizer(failed_summary_model),
            plan_builder=failed_builder,
        )

        try:
            failed_compactor.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "retained_context_too_large"
        else:
            raise AssertionError("无法保留最小上下文时应停止")

        assert failed_summary_model.calls == 0
        assert failed_store.path.read_bytes() == before
        print("规划失败时不请求摘要、不写入节点：通过")


if __name__ == "__main__":
    main()