from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
import json
from tests.context.check_session_compactor import FakeSummaryModel
from forgepilot.sessions.models import CompactionEntry
from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.cache.snapshot import SummarySnapshotStore
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.summarization.artifacts import (
    SummaryArtifactSource,
    validate_summary_result_sources,
)

def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = SessionStore(root / "session.jsonl")
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        ))

        store.append_message({
            "role": "user",
            "content": "检查工具输出",
        })
        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "call_a",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        })

        artifacts = ArtifactStore(root / "artifacts")
        content = "完整输出\n" * 4000
        ref = artifacts.save_text(content)

        tool_result = store.append_message({
            "role": "tool",
            "tool_call_id": "call_a",
            "tool_name": "read_file",
            "content": content,
            "is_error": False,
            "exit_code": 0,
            "artifact": ref.model_dump(),
        })

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "已读取",
        })

        plan = plan_compaction(
            store.get_current_branch(),
            keep_recent_groups=1,
        )
        assert plan is not None

        before = store.path.read_bytes()
        model = FakeSummaryModel()

        summarizer = ContextSummarizer(
            model,
            artifact_store=artifacts,
        )

        result = summarizer.summarize_with_sources(plan)

        assert isinstance(result.summary, ContextSummary)
        assert len(result.artifact_sources) == 1

        source = result.artifact_sources[0]
        assert source.source_entry_id == tool_result.id
        assert source.artifact == ref
        assert source.has_partial_view is True
        assert model.calls == 1
        assert store.path.read_bytes() == before
        print("摘要与片段来源一起返回，不写入历史：通过")

        # 不启用摘要 Snip，提供完整正文。
        full_summarizer = ContextSummarizer(FakeSummaryModel())
        full_result = full_summarizer.summarize_with_sources(plan)

        assert full_result.artifact_sources[0].has_partial_view is False
        print("完整输入不会错误标记为片段：通过")

        inherited = full_summarizer.summarize_with_sources(
            plan,
            previous_sources=result.artifact_sources,
        )

        assert len(inherited.artifact_sources) == 1
        assert inherited.artifact_sources[0].has_partial_view is True
        print("继承来源，并保留曾使用片段的事实：通过")

        legacy_summary = summarizer.summarize(plan)
        assert isinstance(legacy_summary, ContextSummary)
        print("原 summarize 接口仍然兼容：通过")

        snapshot_path = root / "candidate.json"

        def make_cache():
            return SummaryCandidateCache(
                profile={"test_version": 2},
                snapshot_store=SummarySnapshotStore(snapshot_path),
            )

        def reject_publication(branch, entry):
            raise ModelError("test_rejected", "模拟暂时不能发布")

        cache = make_cache()
        before_publication = store.path.read_bytes()
        calls_before = model.calls

        first_compactor = SessionCompactor(
            store,
            summarizer,
            candidate_cache=cache,
            publication_check=reject_publication,
        )

        try:
            first_compactor.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "test_rejected"
        else:
            raise AssertionError("第一次应拒绝发布")

        assert model.calls == calls_before + 1
        assert store.path.read_bytes() == before_publication

        cached = cache.get_result(plan)
        assert cached is not None
        assert cached.artifact_sources == result.artifact_sources

        # 修改取出的副本，不影响内存缓存中的来源列表。
        cached.artifact_sources.clear()
        assert (
                cache.get_result(plan).artifact_sources
                == result.artifact_sources
        )
        print("完整候选进入缓存，副本修改不影响原件：通过")

        # 新模型、新缓存，模拟重启后从磁盘复用候选。
        reopened_model = FakeSummaryModel()
        second_compactor = SessionCompactor(
            store,
            ContextSummarizer(
                reopened_model,
                artifact_store=artifacts,
            ),
            candidate_cache=make_cache(),
        )

        compaction = second_compactor.compact(
            keep_recent_groups=1,
        )

        assert compaction is not None
        assert reopened_model.calls == 0
        assert compaction.artifact_sources == result.artifact_sources
        print("磁盘缓存命中后发布，来源不丢失：通过")

        reopened = SessionStore(store.path)
        saved_compaction = reopened.get_current_branch()[-1]

        assert isinstance(saved_compaction, CompactionEntry)
        assert saved_compaction.artifact_sources == result.artifact_sources
        print("来源列表随压缩节点保存和恢复：通过")

        reopened.append_message({
            "role": "user",
            "content": "继续检查",
        })

        next_plan = plan_compaction(
            reopened.get_current_branch(),
            keep_recent_groups=1,
        )

        assert next_plan is not None
        assert (
            next_plan.previous_artifact_sources
            == result.artifact_sources
        )
        assert (
            next_plan.previous_artifact_sources
            is not saved_compaction.artifact_sources
        )
        print("下一次压缩计划继承旧来源：通过")
        next_model = FakeSummaryModel()
        next_result = ContextSummarizer(
            next_model
        ).summarize_with_sources(next_plan)

        validate_summary_result_sources(next_plan, next_result)
        print("完整继承的来源列表通过校验：通过")

        missing = SummarizationResult(
            summary=next_result.summary,
            artifact_sources=[],
        )

        original_source = next_result.artifact_sources[0]
        cleared_partial = SummaryArtifactSource.model_validate({
            **original_source.model_dump(),
            "has_partial_view": False,
        })

        lost_partial = SummarizationResult(
            summary=next_result.summary,
            artifact_sources=[cleared_partial],
        )

        duplicated = SummarizationResult(
            summary=next_result.summary,
            artifact_sources=[
                original_source,
                original_source,
            ],
        )

        for invalid_result in (missing, lost_partial, duplicated):
            try:
                validate_summary_result_sources(
                    next_plan,
                    invalid_result,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("不完整来源被接受")

        print("拒绝遗漏、重复和片段历史丢失：通过")

        # 模拟缓存里有摘要文字，但遗漏了来源列表。
        invalid_cache = SummaryCandidateCache(
            profile={"test": "incomplete_sources"},
        )
        invalid_cache.put_result(next_plan, missing)

        unused_model = FakeSummaryModel()
        invalid_compactor = SessionCompactor(
            reopened,
            ContextSummarizer(unused_model),
            candidate_cache=invalid_cache,
        )

        saved = reopened.path.read_bytes()

        try:
            invalid_compactor.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "invalid_summary_sources"
        else:
            raise AssertionError("不完整缓存不应发布")

        assert unused_model.calls == 0
        assert reopened.path.read_bytes() == saved
        print("不完整缓存命中后停止发布，不修改历史：通过")

        assert compaction.artifact_sources_version == 2

        original_records = [
            json.loads(line)
            for line in reopened.path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]

        damaged_records = json.loads(
            json.dumps(original_records)
        )

        for item in damaged_records:
            if item.get("id") == compaction.id:
                item["artifact_sources"] = []

        damaged_path = root / "damaged-session.jsonl"
        damaged_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n"
                for item in damaged_records
            ),
            encoding="utf-8",
        )

        try:
            SessionStore(damaged_path).read()
        except ValueError as exc:
            assert "遗漏" in str(exc)
        else:
            raise AssertionError("来源遗漏的新版历史不应被接受")

        print("重新打开时拒绝来源遗漏的新版节点：通过")

        # 模拟没有新版本元数据的旧会话记录。
        legacy_records = json.loads(
            json.dumps(original_records)
        )

        for item in legacy_records:
            if item.get("id") == compaction.id:
                item.pop("artifact_sources_version", None)
                item.pop("artifact_sources", None)

        legacy_path = root / "legacy-session.jsonl"
        legacy_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n"
                for item in legacy_records
            ),
            encoding="utf-8",
        )

        legacy_store = SessionStore(legacy_path)
        _, legacy_nodes = legacy_store.read()

        legacy_compaction = next(
            node
            for node in legacy_nodes
            if node.id == compaction.id
        )

        assert legacy_compaction.artifact_sources_version == 1
        assert legacy_compaction.artifact_sources == []
        print("旧会话仍可读取，不冒充完整来源记录：通过")


if __name__ == "__main__":
    main()