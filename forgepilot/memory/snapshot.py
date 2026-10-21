from dataclasses import dataclass

from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import require_complete_tools


@dataclass(frozen=True)
class MemorySnapshot:
    session_id: str
    source_leaf_id: str
    user_entry_id: str
    entries: tuple[MessageEntry, ...]

def build_memory_snapshot(
    store: SessionStore,
    *,
    require_final: bool = True,
) -> MemorySnapshot:
    header, _ = store.read()
    branch = store.get_current_branch()

    # 复用已有检查：不能在工具结果尚未收齐时提取。
    require_complete_tools(branch)

    messages = [
        entry
        for entry in branch
        if isinstance(entry, MessageEntry)
    ]

    if not messages:
        raise ValueError("当前分支没有可提取的消息")

    last = messages[-1].payload

    if require_final and (
            last.get("role") != "assistant"
            or last.get("type") != "final"
    ):
        raise ValueError("本轮尚未完成，不能提取长期记忆")

    users = [
        entry
        for entry in messages
        if entry.payload.get("role") == "user"
    ]

    if not users:
        raise ValueError("当前分支没有用户消息")

    return MemorySnapshot(
        session_id=header.session_id,
        source_leaf_id=branch[-1].id,
        user_entry_id=users[-1].id,
        entries=tuple(
            entry.model_copy(deep=True)
            for entry in messages
        ),
    )