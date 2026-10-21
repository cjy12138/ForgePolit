from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.planning import CompactionPlan, plan_compaction
from forgepilot.sessions.context import (
    build_request_messages,
    select_context_entries,
)
from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import (
    CompactionEntry,
    MessageEntry,
    SessionNode,
)
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.validation import require_complete_tools
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.compaction.collapse_boundary import (
    align_compaction_with_collapse,
)
def plan_compaction_by_tokens(
    entries: list[SessionNode],
    *,
    model,
    tools,
    target_input_tokens: int,
    summary_reserve_tokens: int,
    collapse_records: list[CollapseRecord] | None = None,
) -> CompactionPlan | None:
    collapse_records = (
        collapse_records if collapse_records is not None else []
    )
    for name, value in (
            ("target_input_tokens", target_input_tokens),
            ("summary_reserve_tokens", summary_reserve_tokens),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} 必须是正整数")

    if summary_reserve_tokens >= target_input_tokens:
        raise ValueError("摘要预留必须小于目标输入预算")

    require_complete_tools(entries)

    if not entries or isinstance(entries[-1], CompactionEntry):
        return None

    visible = [
        node
        for node in select_context_entries(entries)
        if isinstance(node, MessageEntry)
    ]

    groups = group_entries(visible)

    if len(groups) < 2:
        return None

    empty_summary = ContextSummary(**{
        name: []
        for name in ContextSummary.model_fields
    })

    # 从保留最多近期组开始，逐步减少。
    # 至少留一组，同时至少压缩一组。
    for keep_count in range(len(groups) - 1, 0, -1):
        plan = plan_compaction(
            entries,
            keep_recent_groups=keep_count,
        )
        assert plan is not None

        plan = align_compaction_with_collapse(
            plan,
            entries,
            collapse_records,
        )

        if plan is None:
            continue

        # 仅用来估算未来请求的临时节点，不写入会话。
        placeholder = CompactionEntry(
            session_id=entries[-1].session_id,
            parent_id=plan.source_leaf_id,
            source_leaf_id=plan.source_leaf_id,
            first_kept_entry_id=plan.first_kept_entry_id,
            summary=empty_summary,
            artifact_sources=plan.previous_artifact_sources,
        )

        messages = build_request_messages([
            *entries,
            placeholder,
        ])

        estimated = model.estimate_input_tokens(
            messages=messages,
            tools=tools,
        )

        if (
                estimated + summary_reserve_tokens
                <= target_input_tokens
        ):
            return plan

    raise ModelError(
        "retained_context_too_large",
        (
            "原始要求、工具定义、最近一个完整交互组"
            "及摘要预留仍超过目标预算，无法选择保留边界。"
        ),
    )
