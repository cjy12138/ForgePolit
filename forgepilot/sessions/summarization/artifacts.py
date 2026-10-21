from __future__ import annotations

import hashlib
from typing import Any, TYPE_CHECKING

from forgepilot.sessions.snip.records import hash_tool_payload

if TYPE_CHECKING:
    from forgepilot.sessions.models import MessageEntry
    from forgepilot.sessions.compaction.planning import CompactionPlan
    from forgepilot.sessions.summarization.result import SummarizationResult

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.artifacts.store import ArtifactRef




class SummaryArtifactSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )

    source_entry_id: str = Field(
        min_length=1,
        description="完整工具结果的原始消息节点 ID。",
    )

    source_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="原始工具结果 payload 的哈希。",
    )

    artifact: ArtifactRef

    has_partial_view: bool = Field(
        description="摘要链中是否曾只提供该结果的截断视图。",
    )

def merge_summary_artifacts(
    previous: list[SummaryArtifactSource],
    current: list[SummaryArtifactSource],
) -> list[SummaryArtifactSource]:
    merged: dict[str, SummaryArtifactSource] = {}

    for source in [*previous, *current]:
        existing = merged.get(source.source_entry_id)

        if existing is None:
            merged[source.source_entry_id] = source.model_copy(
                deep = True,
            )
            continue

        if (
            existing.source_sha256 != source.source_sha256
            or existing.artifact != source.artifact
        ):
            raise ValueError("同一摘要来源出现了不一致的原文或产物")

        merged[source.source_entry_id] = SummaryArtifactSource(
            source_entry_id=source.source_entry_id,
            source_sha256=source.source_sha256,
            artifact=source.artifact.model_copy(deep=True),
            has_partial_view=(
                    existing.has_partial_view
                    or source.has_partial_view
            ),
        )
    return list(merged.values())

def collect_summary_artifacts(
    entries: list[MessageEntry],
    views: list[dict[str, Any]],
) -> list[SummaryArtifactSource]:
    sources = []

    for entry, item in zip(entries, views, strict=True):
        if item["id"] != entry.id:
            raise ValueError("摘要视图与原始消息 ID 不对应")

        original = entry.payload
        view = item["message"]

        if original.get("role") != "tool":
            continue

        if original.get("snipped") is True:
            raise ValueError("摘要来源必须是完整原始消息")

        if "artifact" not in original:
            continue

        ref = ArtifactRef.model_validate(original["artifact"])

        content = original.get("content")
        if not isinstance(content, str):
            raise ValueError("原始工具输出必须是字符串")

        data = content.encode("utf-8")

        if (
            len(data) != ref.size_bytes
            or hashlib.sha256(data).hexdigest() != ref.sha256
        ):
            raise ValueError("原始工具输出与产物引用不一致")

        partial = view.get("snipped") is True

        if partial:
            if not isinstance(view.get("content"), str):
                raise ValueError("摘要片段必须是字符串")

            # 截断只允许改变正文和添加 snipped 标记。
            view_metadata = {
                key: value
                for key, value in view.items()
                if key not in {"content", "snipped"}
            }
            original_metadata = {
                key: value
                for key, value in original.items()
                if key not in {"content", "snipped"}
            }

            if view_metadata != original_metadata:
                raise ValueError("摘要片段改变了工具结果元数据")

        elif view != original:
            raise ValueError("完整摘要视图与原始消息不一致")

        sources.append(SummaryArtifactSource(
            source_entry_id=entry.id,
            source_sha256=hash_tool_payload(original),
            artifact=ref,
            has_partial_view=partial,
        ))

    return merge_summary_artifacts([], sources)

def validate_summary_artifact_sources(
    sources: list[SummaryArtifactSource],
    entries: list[MessageEntry],
) -> None:
    by_id = {entry.id: entry for entry in entries}
    seen = set()

    for source in sources:
        if source.source_entry_id in seen:
            raise ValueError("摘要产物来源重复")

        seen.add(source.source_entry_id)
        entry = by_id.get(source.source_entry_id)

        if entry is None:
            raise ValueError("摘要产物来源不在已压缩历史中")

        # 使用完整原文重新检查产物引用与内容。
        expected = collect_summary_artifacts(
            [entry],
            [{
                "id": entry.id,
                "message": entry.payload,
            }],
        )

        if len(expected) != 1:
            raise ValueError("摘要产物来源不是带产物的工具结果")

        if (
            source.source_sha256 != expected[0].source_sha256
            or source.artifact != expected[0].artifact
        ):
            raise ValueError("摘要产物来源与原始历史不一致")

def validate_summary_result_sources(
    plan: CompactionPlan,
    result: SummarizationResult,
) -> None:
    # 这里使用全文，仅用于计算应保留哪些来源。
    # 不能用它反推实际摘要请求是否提供了片段。
    current_sources = collect_summary_artifacts(
        plan.to_summarize,
        [
            {
                "id": entry.id,
                "message": entry.payload,
            }
            for entry in plan.to_summarize
        ],
    )

    expected_sources = merge_summary_artifacts(
        plan.previous_artifact_sources,
        current_sources,
    )

    expected = {
        source.source_entry_id: source
        for source in expected_sources
    }
    actual = {
        source.source_entry_id: source
        for source in result.artifact_sources
    }

    if len(actual) != len(result.artifact_sources):
        raise ValueError("摘要产物来源重复")

    if actual.keys() != expected.keys():
        raise ValueError("摘要产物来源存在遗漏或额外来源")

    for entry_id, expected_source in expected.items():
        actual_source = actual[entry_id]

        if (
            actual_source.source_sha256
            != expected_source.source_sha256
            or actual_source.artifact != expected_source.artifact
        ):
            raise ValueError("摘要产物来源与计划不一致")

        if (
            expected_source.has_partial_view
            and not actual_source.has_partial_view
        ):
            raise ValueError("摘要产物来源丢失了片段使用历史")
