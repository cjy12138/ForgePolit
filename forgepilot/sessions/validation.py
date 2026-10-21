from forgepilot.contracts import response_adapter
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.models import CompactionEntry, SessionNode

def pending_tool_calls(entries: list[SessionNode]) -> set[str]:
    pending: set[str] = set()

    for entry in entries:
        if isinstance(entry, CompactionEntry):
            if pending:
                raise ValueError("工具结果尚未收齐，不能插入压缩节点")
            continue

        message = entry.payload
        # 后面的原代码不变。
        role = message.get("role")

        if role  == "tool":
            call_id = message.get("tool_call_id")

            if not isinstance(call_id, str) or call_id not in pending:
                raise ValueError("工具结果没有对应的待完成调用")

            pending.remove(call_id)
            continue

        # 工具结果未收齐，不允许插入新一轮消息。
        if pending:
            raise ValueError("工具结果尚未收齐，出现了新的非工具消息")

        if role == "assistant":
            # role 是消息外层字段，响应校验器不接受它。
            response = response_adapter.validate_python({
                key: value
                for key, value in message.items()
                if key != "role"
            })

            if response.type == "tool_call":
                pending.add(response.id)

            elif response.type == "tool_batch":
                pending.update(
                    call.id
                    for call in response.calls
                )

        elif role not in {"user", "system"}:
            raise ValueError(f"不支持的消息角色：{role}")

    return pending

def require_complete_tools(entries: list[SessionNode]) -> None:
    pending = pending_tool_calls(entries)

    if pending:
        raise ValueError(
            "当前分支仍有未完成的工具调用："
            + ", ".join(sorted(pending))
        )
