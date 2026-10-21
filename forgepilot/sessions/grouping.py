
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.validation import require_complete_tools


def group_entries(
    entries: list[MessageEntry],
) -> list[list[MessageEntry]]:
    # 先确保顺序合法，且所有工具调用都有对应结果。
    require_complete_tools(entries)

    groups: list[list[MessageEntry]] = []
    current: list[MessageEntry] = []
    pending: set[str] = set()

    for entry in entries:
        message = entry.payload
        role = message["role"]

        current.append(entry)

        if role == "assistant":
            if message["type"]=="tool_call":
                pending.add(message["id"])

            elif message["type"]=="tool_batch":
                pending.update(
                    call["id"]
                    for call in message["calls"]
                )

        elif role == "tool":
           pending.remove(message["tool_call_id"])

        if not pending :
            groups.append(current)
            current = []

    return groups