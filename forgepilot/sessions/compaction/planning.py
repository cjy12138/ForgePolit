from dataclasses import dataclass, field

from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import (
    CompactionEntry,
    MessageEntry,
)
from forgepilot.sessions.summarization.schema import validate_summary_sources
from forgepilot.sessions.context import select_context_entries
from forgepilot.sessions.models import SessionNode
from forgepilot.sessions.validation import require_complete_tools
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.summarization.artifacts import SummaryArtifactSource
from forgepilot.sessions.summarization.artifacts import (
    validate_summary_artifact_sources,
)
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.summarization.artifacts import (
    validate_summary_result_sources,
)

@dataclass
class CompactionPlan:
    source_leaf_id: str
    to_summarize: list[MessageEntry]
    to_keep: list[MessageEntry]

    previous_summary: ContextSummary | None = None
    previous_compaction_id: str | None = None

    previous_artifact_sources: list[SummaryArtifactSource] = field(
        default_factory=list,
    )

    @property
    def first_kept_entry_id(self) -> str:
        return self.to_keep[0].id

def plan_compaction(
    entries: list[SessionNode],
    *,
    keep_recent_groups: int,
) -> CompactionPlan | None:
    if (
        type(keep_recent_groups) is not int
        or keep_recent_groups < 1
    ):
        raise ValueError("keep_recent_groups 必须是正整数")

    require_complete_tools(entries)

    if not entries:
        return None

    # 刚压缩完，没有新增消息，不立即再压缩一次。
    if isinstance(entries[-1], CompactionEntry):
        return None

    selected = select_context_entries(entries)

    previous_compaction = next(
        (
            entry
            for entry in selected
            if isinstance(entry, CompactionEntry)
        ),
        None,
    )

    visible_messages = [
        entry
        for entry in selected
        if isinstance(entry, MessageEntry)
    ]

    groups = group_entries(visible_messages)

    if len(groups) <= keep_recent_groups:
        return None

    split_index = len(groups) - keep_recent_groups

    to_summarize = [
        entry
        for group in groups[:split_index]
        for entry in group
    ]

    to_keep = [
        entry
        for group in groups[split_index:]
        for entry in group
    ]

    return CompactionPlan(
        source_leaf_id=entries[-1].id,
        to_summarize=to_summarize,
        to_keep=to_keep,
        previous_summary=(
            previous_compaction.summary.model_copy(deep=True)
            if previous_compaction is not None
            else None
        ),
        previous_compaction_id=(
            previous_compaction.id
            if previous_compaction is not None
            else None
        ),
        previous_artifact_sources=(
            [
                source.model_copy(deep=True)
                for source in previous_compaction.artifact_sources
            ]
            if previous_compaction is not None
            else []
        ),
    )

def validate_compaction_entry(
    entry: CompactionEntry,
    branch: list[SessionNode],
) -> None:
    if not branch:
        raise ValueError("空分支不能压缩")

    if entry.source_leaf_id != branch[-1].id:
        raise ValueError("来源终点与当前分支不一致")

    if any(
        node.session_id != entry.session_id
        for node in branch
    ):
        raise ValueError("压缩节点与来源消息不属于同一会话")

    if any(node.id == entry.id for node in branch):
        raise ValueError("压缩节点 ID 与已有节点重复")

    require_complete_tools(branch)

    # 原始消息用于验证完整交互边界和来源引用。
    raw_messages = [
        node
        for node in branch
        if isinstance(node, MessageEntry)
    ]

    groups = group_entries(raw_messages)
    group_starts = {group[0].id for group in groups}

    if entry.first_kept_entry_id not in group_starts:
        raise ValueError(
            "保留起点必须是当前分支中一个完整交互组的起点"
        )

    # 连续压缩只能从当前有效原文中选择保留起点，
    # 不能退回已经被上一份摘要替代的旧区域。
    visible_messages = [
        node
        for node in select_context_entries(branch)
        if isinstance(node, MessageEntry)
    ]

    visible_ids = [node.id for node in visible_messages]

    if entry.first_kept_entry_id not in visible_ids:
        raise ValueError("保留起点不在当前有效上下文中")

    if visible_ids.index(entry.first_kept_entry_id) == 0:
        raise ValueError("没有需要压缩的旧消息")

    raw_kept_index = next(
        index
        for index, node in enumerate(raw_messages)
        if node.id == entry.first_kept_entry_id
    )

    validate_summary_sources(
        entry.summary,
        raw_messages[:raw_kept_index],
    )

    validate_summary_artifact_sources(
        entry.artifact_sources,
        raw_messages[:raw_kept_index],
    )
    if entry.artifact_sources_version == 2:
        kept_index = visible_ids.index(entry.first_kept_entry_id)

        previous_compaction = next(
            (
                node
                for node in select_context_entries(branch)
                if isinstance(node, CompactionEntry)
            ),
            None,
        )

        source_plan = CompactionPlan(
            source_leaf_id=entry.source_leaf_id,
            to_summarize=visible_messages[:kept_index],
            to_keep=visible_messages[kept_index:],
            previous_artifact_sources=(
                previous_compaction.artifact_sources
                if previous_compaction is not None
                else []
            ),
        )

        validate_summary_result_sources(
            source_plan,
            SummarizationResult(
                summary=entry.summary,
                artifact_sources=entry.artifact_sources,
            ),
        )
