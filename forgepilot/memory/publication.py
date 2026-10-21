from forgepilot.memory.models import (
    MemoryDocument,
    MemoryMetadata,
    MemoryProposal,
)
from forgepilot.memory.snapshot import MemorySnapshot
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.memory.validation import validate_memory_proposal
from forgepilot.sessions.store import SessionStore
import hashlib
import json

from forgepilot.memory.models import MemorySource

def publish_new_memories(
    proposal: MemoryProposal,
    *,
    snapshot: MemorySnapshot,
    sessions: SessionStore,
    memory_store: MarkdownMemoryStore,
) -> list[MemoryDocument]:
    proposal = validate_memory_proposal(
        proposal,
        snapshot=snapshot,
        sessions=sessions,
    )

    existing = set(memory_store.list_filenames())

    # 先检查整批候选，不能直接覆盖已有主题。
    for candidate in proposal.memories:
        if candidate.filename in existing:
            raise ValueError(
                f"主题 {candidate.filename} 已存在，"
                "需要通过更新流程发布"
            )

    documents = [
        MemoryDocument(
            filename=candidate.filename,
            metadata=MemoryMetadata(
                name=candidate.name,
                type=candidate.type,
                description=candidate.description,
                source_session_id=snapshot.session_id,
                source_entry_ids=list(
                    candidate.source_entry_ids
                ),
            ),
            content=candidate.content,
        )
        for candidate in proposal.memories
    ]

    memory_store.apply_changes(documents, index_change=proposal.index_change)

    return documents

def publish_memory_updates(
    proposal: MemoryProposal,
    *,
    snapshot: MemorySnapshot,
    sessions: SessionStore,
    memory_store: MarkdownMemoryStore,
    read_versions: dict[str, str],
) -> list[MemoryDocument]:
    proposal = validate_memory_proposal(
        proposal,
        snapshot=snapshot,
        sessions=sessions,
    )

    documents = []

    # 先准备并检查整批更新，再开始保存。
    for candidate in proposal.memories:
        expected = read_versions.get(candidate.filename)

        if expected is None:
            raise ValueError(
                f"更新前必须读取主题：{candidate.filename}"
            )

        old = memory_store.read(candidate.filename)

        serialized = json.dumps(
            old.model_dump(),
            ensure_ascii=False,
            sort_keys=True,
        )
        actual = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

        if actual != expected:
            raise ValueError(
                f"主题已变化，需要重新提取：{candidate.filename}"
            )

        previous_sources = [
            source.model_copy(deep=True)
            for source in old.metadata.previous_sources
        ]

        old_source = MemorySource(
            source_session_id=old.metadata.source_session_id,
            source_entry_ids=list(
                old.metadata.source_entry_ids
            ),
        )

        if old_source not in previous_sources:
            previous_sources.append(old_source)

        documents.append(MemoryDocument(
            filename=candidate.filename,
            metadata=MemoryMetadata(
                name=candidate.name,
                type=candidate.type,
                description=candidate.description,
                source_session_id=snapshot.session_id,
                source_entry_ids=list(
                    candidate.source_entry_ids
                ),
                previous_sources=previous_sources,
            ),
            content=candidate.content,
        ))

    memory_store.apply_changes(
        documents,
        index_change=proposal.index_change,
        backup=bool(documents),
        backup_kind="extract",
        proposal_json=proposal.model_dump_json(indent=2),
    )

    return documents


def _publish_memories_unlocked(
    proposal: MemoryProposal,
    *,
    snapshot: MemorySnapshot,
    sessions: SessionStore,
    memory_store: MarkdownMemoryStore,
    read_versions: dict[str, str],
) -> list[MemoryDocument]:
    proposal = validate_memory_proposal(
        proposal,
        snapshot=snapshot,
        sessions=sessions,
    )

    existing = set(memory_store.list_filenames())
    documents = []

    for candidate in proposal.memories:
        filename = candidate.filename
        previous_sources = []

        if filename in existing:
            expected = read_versions.get(filename)

            if expected is None:
                raise ValueError(
                    f"更新前必须读取主题：{filename}"
                )

            old = memory_store.read(filename)

            serialized = json.dumps(
                old.model_dump(),
                ensure_ascii=False,
                sort_keys=True,
            )
            actual = hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest()

            if actual != expected:
                raise ValueError(
                    f"主题已变化，需要重新提取：{filename}"
                )

            previous_sources = [
                source.model_copy(deep=True)
                for source in old.metadata.previous_sources
            ]

            old_source = MemorySource(
                source_session_id=old.metadata.source_session_id,
                source_entry_ids=list(
                    old.metadata.source_entry_ids
                ),
            )

            if old_source not in previous_sources:
                previous_sources.append(old_source)

        elif filename in read_versions:
            # 读取过的旧主题现在消失了，不能当作新建。
            raise ValueError(
                f"主题已删除，需要重新提取：{filename}"
            )

        documents.append(MemoryDocument(
            filename=filename,
            metadata=MemoryMetadata(
                name=candidate.name,
                type=candidate.type,
                description=candidate.description,
                source_session_id=snapshot.session_id,
                source_entry_ids=list(
                    candidate.source_entry_ids
                ),
                previous_sources=previous_sources,
            ),
            content=candidate.content,
        ))

    # 前面的候选检查全部通过后，才开始写入。
    memory_store.apply_changes(
        documents,
        index_change=proposal.index_change,
        backup=any(document.filename in existing for document in documents),
        backup_kind="extract",
        proposal_json=proposal.model_dump_json(indent=2),
    )

    return documents

def publish_memories(
    proposal: MemoryProposal,
    *,
    snapshot: MemorySnapshot,
    sessions: SessionStore,
    memory_store: MarkdownMemoryStore,
    read_versions: dict[str, str],
) -> list[MemoryDocument]:
    with memory_store.write_lock():
        return _publish_memories_unlocked(
            proposal,
            snapshot=snapshot,
            sessions=sessions,
            memory_store=memory_store,
            read_versions=read_versions,
        )
