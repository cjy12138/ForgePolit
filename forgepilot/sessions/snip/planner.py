from platform import node

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.context import select_context_entries
from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import MessageEntry, SessionNode
from forgepilot.sessions.snip.transform import snip_tool_message
from forgepilot.sessions.snip.records import (
    SnipRecord,
    hash_tool_payload,
)
from forgepilot.sessions.validation import require_complete_tools

def plan_snip_records(
    branch: list[SessionNode],
    artifacts: ArtifactStore,
) -> list[SnipRecord]:
    require_complete_tools(branch)

    if not branch:
        return []

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

    # 与当前请求视图保持一致：保护最近两组工具交互。
    protected_ids = {
        node.id
        for group in tool_groups[-2:]
        for node in group
    }

    candidates = []

    for node in visible:
        message = node.payload

        if (
                node.id in protected_ids
                or message.get("role") != "tool"
                or message.get("is_error") is not False
                or message.get("exit_code") not in (None, 0)
                or message.get("tool_name") == "read_artifact"
        ):
            continue

        shortened = snip_tool_message(message, artifacts)

        # 原文较短，或截断后的元数据开销太大，就不生成记录。
        if shortened == message:
            continue

        candidates.append(SnipRecord(
            session_id=node.session_id,
            source_leaf_id=branch[-1].id,
            target_entry_id=node.id,
            source_sha256=hash_tool_payload(message),
        ))

    return candidates




