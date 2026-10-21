import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.cache.snapshot import SummarySnapshotStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


def reject_publication(branch, entry):
    raise ModelError("test_rejected", "模拟拒绝发布")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = make_store(directory, "session.jsonl")
        path = root / "candidate.json"
        before = store.path.read_bytes()

        def make_cache(profile_version=1):
            return SummaryCandidateCache(
                profile={"test_profile_version": profile_version},
                snapshot_store=SummarySnapshotStore(path),
            )

        first_model = FakeSummaryModel()
        first = SessionCompactor(
            store,
            ContextSummarizer(first_model),
            candidate_cache=make_cache(),
            publication_check=reject_publication,
        )

        try:
            first.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "test_rejected"

        assert first_model.calls == 1
        assert path.is_file()
        assert store.path.read_bytes() == before

        # 新缓存和新模型对象，模拟重启。
        second_model = FakeSummaryModel()
        reopened = SessionCompactor(
            store,
            ContextSummarizer(second_model),
            candidate_cache=make_cache(),
            publication_check=reject_publication,
        )

        try:
            reopened.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "test_rejected"

        assert second_model.calls == 0
        print("重建对象后复用磁盘候选，仍执行发布检查：通过")

        from forgepilot.sessions.compaction.planning import plan_compaction

        plan = plan_compaction(
            store.get_current_branch(),
            keep_recent_groups=1,
        )
        assert plan is not None

        assert make_cache(profile_version=2).get(plan) is None
        print("配置变化后不复用旧快照：通过")

        # 修改摘要正文，但不更新校验值。
        data = json.loads(path.read_text(encoding="utf-8"))
        data["summary"]["goal"][0]["text"] = "被改变的摘要"
        path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        assert make_cache().get(plan) is None
        print("摘要内容损坏后按未命中处理：通过")

        path.write_text('{"broken":', encoding="utf-8")
        assert make_cache().get(plan) is None
        print("快照 JSON 损坏后按未命中处理：通过")


if __name__ == "__main__":
    main()