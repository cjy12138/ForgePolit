from forgepilot.memory.dream_models import DreamProposal
from forgepilot.memory.dream_validation import validate_dream_proposal
from forgepilot.memory.models import (
    MemoryDocument,
    MemoryMetadata,
    MemorySource,
)
from forgepilot.memory.store import MarkdownMemoryStore

def prepare_dream_documents(
    proposal: DreamProposal,
    *,
    memory_store: MarkdownMemoryStore,
    read_versions: dict[str, str],
    fully_read: set[str],
) -> list[MemoryDocument]:
    with memory_store.write_lock():
        proposal = validate_dream_proposal(
            proposal,
            memory_store=memory_store,
            read_versions=read_versions,
            fully_read=fully_read,
        )

        existing = set(memory_store.list_filenames())
        documents = []

        for rewrite in proposal.rewrites:
            # 更新已有主题时，优先保留它原来的主要来源。
            filenames = list(rewrite.source_files)

            if rewrite.filename in existing:
                filenames = [
                    rewrite.filename,
                    *[
                        filename
                        for filename in filenames
                        if filename != rewrite.filename
                    ],
                ]

            sources: list[MemorySource] = []

            for filename in filenames:
                original = memory_store.read(filename)

                primary = MemorySource(
                    source_session_id=(
                        original.metadata.source_session_id
                    ),
                    source_entry_ids=list(
                        original.metadata.source_entry_ids
                    ),
                )

                inherited = [
                    primary,
                    *original.metadata.previous_sources,
                ]

                for source in inherited:
                    if source not in sources:
                        sources.append(
                            source.model_copy(deep=True)
                        )

            primary = sources[0]

            documents.append(MemoryDocument(
                filename=rewrite.filename,
                metadata=MemoryMetadata(
                    name=rewrite.name,
                    type=rewrite.type,
                    description=rewrite.description,
                    source_session_id=primary.source_session_id,
                    source_entry_ids=list(
                        primary.source_entry_ids
                    ),
                    previous_sources=sources[1:],
                ),
                content=rewrite.content,
            ))

        return documents

def publish_dream_proposal(
    proposal: DreamProposal,
    *,
    memory_store: MarkdownMemoryStore,
    read_versions: dict[str, str],
    fully_read: set[str],
) -> dict:
    with memory_store.write_lock():
        proposal = DreamProposal.model_validate(
            proposal.model_dump()
        )

        # 在发布锁内重新检查来源，并构建正式文档。
        documents = prepare_dream_documents(
            proposal,
            memory_store=memory_store,
            read_versions=read_versions,
            fully_read=fully_read,
        )

        conflicts = [
            item.model_dump()
            for item in proposal.conflicts
        ]

        has_index_change = proposal.index_change is not None and bool(
            proposal.index_change.add or proposal.index_change.remove
        )
        if not documents and not proposal.deletes and not has_index_change:
            return {
                "status": "no_changes",
                "conflicts": conflicts,
            }

        result = memory_store.apply_changes(
            documents,
            deletes=[item.filename for item in proposal.deletes],
            index_change=proposal.index_change,
            backup=True,
            proposal_json=proposal.model_dump_json(indent=2),
        )

        return {
            "status": "published",
            "rewritten": sorted(item.filename for item in documents),
            "deleted": sorted(item.filename for item in proposal.deletes),
            **result,
            "conflicts": conflicts,
        }
