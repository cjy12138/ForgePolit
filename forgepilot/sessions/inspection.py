from typing import Literal

from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.validation import pending_tool_calls
from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import SessionNode
SessionState = Literal[
    "empty",
    "needs_recovery",
    "completed",
    "ready",
]

def inspect_branch(
    entries: list[SessionNode],
) -> SessionState:
    if pending_tool_calls(entries):
        return "needs_recovery"

    messages = build_messages(entries)

    if not messages:
        return "empty"

    last = messages[-1]

    if (
        last["role"] == "assistant"
        and last.get("type") == "final"
    ):
        return "completed"

    if all(message["role"] == "system" for message in messages):
        return "empty"

    return "ready"