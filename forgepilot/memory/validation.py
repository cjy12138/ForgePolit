from forgepilot.memory.models import MemoryProposal
from forgepilot.memory.snapshot import MemorySnapshot
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore


def validate_memory_proposal(
    proposal: MemoryProposal,
    *,
    snapshot: MemorySnapshot,
    sessions: SessionStore,
) -> MemoryProposal:
    # 重新校验并复制，避免调用者修改对象后绕过限制。
    proposal = MemoryProposal.model_validate(
        proposal.model_dump()
    )

    header, _ = sessions.read()

    if header.session_id != snapshot.session_id:
        raise ValueError("记忆快照不属于当前会话")

    branch = sessions.get_current_branch()
    branch_ids = {entry.id for entry in branch}

    if snapshot.source_leaf_id not in branch_ids:
        raise ValueError("记忆来源已不在当前分支")

    current_messages = {
        entry.id: entry
        for entry in branch
        if isinstance(entry, MessageEntry)
    }

    source_ids = set()

    for entry in snapshot.entries:
        current = current_messages.get(entry.id)

        if (
            current is None
            or current.model_dump() != entry.model_dump()
        ):
            raise ValueError("记忆来源消息与快照不一致")

        source_ids.add(entry.id)

    filenames = [
        candidate.filename
        for candidate in proposal.memories
    ]

    if len(filenames) != len(set(filenames)):
        raise ValueError("同一批候选不能重复更新同一个主题")

    for candidate in proposal.memories:
        ids = candidate.source_entry_ids

        if len(ids) != len(set(ids)):
            raise ValueError("候选引用了重复的来源节点")

        if not set(ids).issubset(source_ids):
            raise ValueError("候选引用了快照之外的来源节点")

    return proposal