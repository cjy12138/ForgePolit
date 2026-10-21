from forgepilot.sessions.collapse.planning import (
    CollapsePlan,
    plan_collapse,
)
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.collapse.validation import (
    validate_collapse_record,
)
from forgepilot.sessions.context import select_context_entries
from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import MessageEntry, SessionNode
from forgepilot.sessions.validation import require_complete_tools


def plan_auto_collapse(
    branch: list[SessionNode],
    records: list[CollapseRecord],
    *,
    max_tool_groups: int = 4,
) -> CollapsePlan | None:
    if (
        type(max_tool_groups) is not int
        or max_tool_groups < 1
    ):
        raise ValueError("max_tool_groups 必须是正整数")

    require_complete_tools(branch)

    if not branch:
        return None

    visible = [
        node
        for node in select_context_entries(branch)
        if isinstance(node, MessageEntry)
    ]
    groups = group_entries(visible)

    tool_groups = [
        group
        for group in groups
        if any(
            node.payload.get("role") == "tool"
            for node in group
        )
    ]

    # 与当前 Collapse 记录校验策略一致。
    protected_ids = {
        node.id
        for group in tool_groups[-2:]
        for node in group
    }

    positions = {
        node.id: index
        for index, node in enumerate(branch)
    }

    covered_ids: set[str] = set()

    for record in records:
        if record.source_leaf_id not in positions:
            continue

        validate_collapse_record(record, branch)

        start = positions[record.source_start_id]
        end = positions[record.source_end_id]

        covered_ids.update(
            node.id
            for node in branch[start:end + 1]
            if isinstance(node, MessageEntry)
        )

    selected: list[MessageEntry] = []
    selected_groups = 0

    for group in groups:
        group_ids = {node.id for node in group}

        has_tool = any(
            node.payload.get("role") == "tool"
            for node in group
        )

        has_requirement = any(
            node.payload.get("role") in {"user", "system"}
            for node in group
        )

        blocked = (
            not has_tool
            or has_requirement
            or bool(group_ids & protected_ids)
            or bool(group_ids & covered_ids)
        )

        if blocked:
            if selected:
                # 已经选中一段后，遇到边界就结束，
                # 避免把不连续的片段拼成一个范围。
                break
            continue

        selected.extend(group)
        selected_groups += 1

        if selected_groups >= max_tool_groups:
            break

    if not selected:
        return None

    # 复用已有范围规划器，统一检查边界并计算来源哈希。
    return plan_collapse(
        branch,
        source_start_id=selected[0].id,
        source_end_id=selected[-1].id,
    )

def list_collapse_candidates(
    branch: list[SessionNode],
    records: list[CollapseRecord],
    *,
    max_tool_groups: int = 4,
) -> list[CollapsePlan]:
    if (
        type(max_tool_groups) is not int
        or max_tool_groups < 1
    ):
        raise ValueError("max_tool_groups 必须是正整数")

    require_complete_tools(branch)

    if not branch:
        return []

    visible = [
        node
        for node in select_context_entries(branch)
        if isinstance(node, MessageEntry)
    ]
    groups = group_entries(visible)

    positions = {
        node.id: index
        for index, node in enumerate(branch)
    }

    covered_ids: set[str] = set()

    for record in records:
        if record.source_leaf_id not in positions:
            continue

        validate_collapse_record(record, branch)

        start = positions[record.source_start_id]
        end = positions[record.source_end_id]

        covered_ids.update(
            node.id
            for node in branch[start:end + 1]
            if isinstance(node, MessageEntry)
        )

    candidates = []

    for start_index in range(len(groups)):
        selected: list[MessageEntry] = []

        for group in groups[
            start_index:start_index + max_tool_groups
        ]:
            has_tool = any(
                node.payload.get("role") == "tool"
                for node in group
            )

            if (
                not has_tool
                or any(node.id in covered_ids for node in group)
            ):
                break

            selected.extend(group)

            try:
                plan = plan_collapse(
                    branch,
                    source_start_id=selected[0].id,
                    source_end_id=selected[-1].id,
                )
            except ValueError:
                # 包含近期保护区等不允许的范围时，
                # 更长的同起点范围也不再考虑。
                break

            candidates.append(plan)

    return candidates