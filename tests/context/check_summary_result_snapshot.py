import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactRef
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.summarization.artifacts import SummaryArtifactSource
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.cache.snapshot import SummarySnapshotStore


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "candidate.json"
        snapshots = SummarySnapshotStore(path)
        key = "a" * 64

        summary = ContextSummary(**{
            name: []
            for name in ContextSummary.model_fields
        })

        source = SummaryArtifactSource(
            source_entry_id="result_a",
            source_sha256="b" * 64,
            artifact=ArtifactRef(
                id="c" * 32,
                sha256="d" * 64,
                size_bytes=10000,
            ),
            has_partial_view=True,
        )

        result = SummarizationResult(
            summary=summary,
            artifact_sources=[source],
        )

        snapshots.save_result(key, result)

        reopened = SummarySnapshotStore(path)
        loaded = reopened.load_result(key)

        assert loaded is not None
        assert loaded.summary == summary
        assert loaded.artifact_sources == [source]
        print("重建对象后恢复摘要和完整来源列表：通过")

        loaded.artifact_sources.clear()

        again = reopened.load_result(key)
        assert again is not None
        assert again.artifact_sources == [source]
        print("修改读取结果不影响磁盘快照：通过")

        data = json.loads(path.read_text(encoding="utf-8"))
        data["artifact_sources"][0]["has_partial_view"] = False

        path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        assert reopened.load_result(key) is None
        print("来源元数据损坏后缓存未命中：通过")

        # 模拟旧版本只有摘要文字的快照。
        snapshots.save_result(key, result)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema_version"] = 1

        path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        assert reopened.load_result(key) is None
        print("旧版本候选快照不冒充完整结果：通过")


if __name__ == "__main__":
    main()