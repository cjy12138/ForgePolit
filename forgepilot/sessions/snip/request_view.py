from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.context import (
    build_request_messages,
    select_context_entries,
)
from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import MessageEntry, SessionNode
from forgepilot.sessions.snip.transform import snip_tool_message
from forgepilot.sessions.snip.records import SnipRecord
from forgepilot.sessions.snip.validation import validate_snip_record

def build_snipped_request(
    branch: list[SessionNode],
    artifacts: ArtifactStore,
    *,
    keep_recent_tool_groups: int = 2,
):
    if (
            type(keep_recent_tool_groups) is not int
            or keep_recent_tool_groups < 1
    ):
        raise ValueError("至少保护一个近期工具交互组")

    # 先按有效上下文选出消息，避免处理已被摘要覆盖的正文。
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

    protected_ids = {
        node.id
        for group in tool_groups[-keep_recent_tool_groups:]
        for node in group
    }

    visible_ids = {node.id for node in visible}
    projected = []

    for node in branch:
        if (
                not isinstance(node, MessageEntry)
                or node.id not in visible_ids
                or node.id in protected_ids
        ):
            projected.append(node)
            continue

        message = node.payload

        if (
                message.get("role") != "tool"
                or message.get("is_error") is not False
                or message.get("exit_code") not in (None, 0)
                or message.get("tool_name") == "read_artifact"
        ):
            projected.append(node)
            continue

        shortened = snip_tool_message(message, artifacts)

        copied = node.model_copy(deep=True)
        copied.payload = shortened
        projected.append(copied)

    return build_request_messages(projected)

def project_recorded_snip_branch(
    branch: list[SessionNode],
    artifacts: ArtifactStore,
    records: list[SnipRecord],
    *,
    keep_recent_tool_groups: int = 2,
):
    if (
        type(keep_recent_tool_groups) is not int
        or keep_recent_tool_groups < 1
    ):
        raise ValueError("至少保护一个近期工具交互组")

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

    protected_ids = {
        node.id
        for group in tool_groups[-keep_recent_tool_groups:]
        for node in group
    }

    branch_ids = {node.id for node in branch}
    visible_ids = {node.id for node in visible}

    records_by_target = {}

    # records 按 JSONL 写入顺序传入。
    # 同一目标有多项适用记录时，使用最后一项。
    for record in records:
        if record.source_leaf_id not in branch_ids:
            continue

        validate_snip_record(record, branch)
        records_by_target[record.target_entry_id] = record

    projected = []

    for node in branch:
        if (
            not isinstance(node, MessageEntry)
            or node.id not in visible_ids
            or node.id in protected_ids
        ):
            projected.append(node)
            continue

        record = records_by_target.get(node.id)

        if record is None:
            projected.append(node)
            continue

        shortened = snip_tool_message(
            node.payload,
            artifacts,
            head_chars=record.head_chars,
            tail_chars=record.tail_chars,
        )

        copied = node.model_copy(deep=True)
        copied.payload = shortened
        projected.append(copied)

    return projected

def build_recorded_snip_request(
    branch: list[SessionNode],
    artifacts: ArtifactStore,
    records: list[SnipRecord],
    *,
    keep_recent_tool_groups: int = 2,
):
    projected = project_recorded_snip_branch(
        branch,
        artifacts,
        records,
        keep_recent_tool_groups=keep_recent_tool_groups,
    )

    return build_request_messages(projected)