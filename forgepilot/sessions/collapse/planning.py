import hashlib
import json
from dataclasses import dataclass

from forgepilot.sessions.context import select_context_entries
from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import MessageEntry, SessionNode
from forgepilot.sessions.validation import require_complete_tools


@dataclass(frozen=True)
class CollapsePlan:
    source_leaf_id: str
    source_start_id: str
    source_end_id: str
    source_sha256: str
    to_summarize: list[MessageEntry]


def collapse_source_digest(entries: list[MessageEntry]) -> str:
    data = [
        entry.model_dump(mode="json")
        for entry in entries
    ]

    encoded = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def plan_collapse(
    branch: list[SessionNode],
    *,
    source_start_id: str,
    source_end_id: str,
    keep_recent_tool_groups: int = 2,
) -> CollapsePlan:
    if (
        type(keep_recent_tool_groups) is not int
        or keep_recent_tool_groups < 1
    ):
        raise ValueError("至少保护一个近期工具交互组")

    require_complete_tools(branch)

    if not branch:
        raise ValueError("空分支不能折叠")

    visible = [
        node
        for node in select_context_entries(branch)
        if isinstance(node, MessageEntry)
    ]

    positions = {
        node.id: index
        for index, node in enumerate(visible)
    }

    if (
        source_start_id not in positions
        or source_end_id not in positions
    ):
        raise ValueError("折叠边界不在当前有效上下文中")

    start = positions[source_start_id]
    end = positions[source_end_id]

    if start > end:
        raise ValueError("折叠起点不能晚于终点")

    groups = group_entries(visible)

    group_starts = {group[0].id for group in groups}
    group_ends = {group[-1].id for group in groups}

    if (
        source_start_id not in group_starts
        or source_end_id not in group_ends
    ):
        raise ValueError("折叠范围必须对齐完整交互组")

    selected = visible[start:end + 1]

    # 原始要求继续明确保留，不放入本阶段的折叠范围。
    if any(
        node.payload.get("role") in {"user", "system"}
        for node in selected
    ):
        raise ValueError("折叠范围不能包含原始用户或系统消息")

    tool_groups = [
        group
        for group in groups
        if any(
            node.payload.get("role") == "tool"
            for node in group
        )
    ]

    protected_ids = {
        node.id
        for group in tool_groups[-keep_recent_tool_groups:]
        for node in group
    }

    if any(node.id in protected_ids for node in selected):
        raise ValueError("折叠范围包含受保护的近期工具交互")

    copied = [
        node.model_copy(deep=True)
        for node in selected
    ]

    return CollapsePlan(
        source_leaf_id=branch[-1].id,
        source_start_id=source_start_id,
        source_end_id=source_end_id,
        source_sha256=collapse_source_digest(copied),
        to_summarize=copied,
    )