from forgepilot.artifacts.store import ArtifactRef
from forgepilot.sessions.summarization.artifacts import (
    SummaryArtifactSource,
    merge_summary_artifacts,
)


def make_source(entry_id, *, partial=False):
    return SummaryArtifactSource(
        source_entry_id=entry_id,
        source_sha256="a" * 64,
        artifact=ArtifactRef(
            id="b" * 32,
            sha256="c" * 64,
            size_bytes=10000,
        ),
        has_partial_view=partial,
    )


def main():
    first = make_source("result_a")
    second = make_source("result_b", partial=True)

    merged = merge_summary_artifacts([first], [second])

    assert [item.source_entry_id for item in merged] == [
        "result_a",
        "result_b",
    ]
    print("继承旧来源，追加新来源：通过")

    partial_first = make_source("result_a", partial=True)
    merged = merge_summary_artifacts([first], [partial_first])

    assert len(merged) == 1
    assert merged[0].has_partial_view is True
    assert first.has_partial_view is False

    # 再合并完整视图记录，也不抹去曾使用片段的事实。
    merged = merge_summary_artifacts(merged, [first])

    assert len(merged) == 1
    assert merged[0].has_partial_view is True
    print("来源去重，并保留片段使用历史：通过")

    restored = SummaryArtifactSource.model_validate_json(
        merged[0].model_dump_json()
    )
    assert restored == merged[0]
    print("元数据可序列化恢复：通过")

    for updates in (
        {"source_sha256": "d" * 64},
        {
            "artifact": ArtifactRef(
                id="e" * 32,
                sha256="c" * 64,
                size_bytes=10000,
            )
        },
    ):
        conflicting = SummaryArtifactSource.model_validate({
            **first.model_dump(),
            **updates,
        })

        try:
            merge_summary_artifacts([first], [conflicting])
        except ValueError:
            pass
        else:
            raise AssertionError("不一致的来源被接受")

    print("拒绝同一来源的哈希或产物冲突：通过")


if __name__ == "__main__":
    main()