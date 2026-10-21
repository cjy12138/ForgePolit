from forgepilot.artifacts.store import ArtifactRef
from forgepilot.sessions.models import MessageEntry, SessionNode
from forgepilot.sessions.snip.records import (
    SnipRecord,
    hash_tool_payload,
)
from forgepilot.sessions.validation import require_complete_tools

def validate_snip_record(
    record: SnipRecord,
    branch: list[SessionNode],
) -> MessageEntry:
    if not branch:
        raise ValueError("Snip 来源分支为空")

    if any(node.session_id != record.session_id for node in branch):
        raise ValueError("Snip 记录与分支不属于同一会话")

    anchor_index = next(
        (
            index
            for index, node in enumerate(branch)
            if node.id == record.source_leaf_id
        ),
        None,
    )

    if anchor_index is None:
        raise ValueError("Snip 来源锚点不在当前分支中")

    # 记录只允许引用当时已经存在的历史。
    source_branch = branch[:anchor_index + 1]
    require_complete_tools(source_branch)

    target = next(
        (
            node
            for node in source_branch
            if node.id == record.target_entry_id
        ),
        None,
    )

    if not isinstance(target, MessageEntry):
        raise ValueError("Snip 目标不是来源分支中的消息节点")

    message = target.payload

    if (
            message.get("role") != "tool"
            or message.get("is_error") is not False
            or message.get("exit_code") not in (None, 0)
            or message.get("tool_name") == "read_artifact"
            or message.get("snipped") is True
    ):
        raise ValueError("Snip 目标不是允许截断的原始工具结果")

    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("工具输出必须是字符串")

    if "artifact" not in message:
        raise ValueError("Snip 目标没有完整输出产物")

    ArtifactRef.model_validate(message["artifact"])

    if hash_tool_payload(message) != record.source_sha256:
        raise ValueError("Snip 来源内容已变化")

    if len(content) <= record.head_chars + record.tail_chars:
        raise ValueError("工具输出不需要截断")

    return target