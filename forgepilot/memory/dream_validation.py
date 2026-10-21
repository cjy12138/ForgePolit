import hashlib
import json

from forgepilot.memory.dream_models import DreamProposal
from forgepilot.memory.models import MemoryDocument
from forgepilot.memory.store import MarkdownMemoryStore


def document_sha256(document: MemoryDocument) -> str:
    serialized = json.dumps(
        document.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def validate_dream_proposal(
    proposal: DreamProposal,
    *,
    memory_store: MarkdownMemoryStore,
    read_versions: dict[str, str],
    fully_read: set[str],
) -> DreamProposal:
    # 重新校验，避免对象创建后被修改。
    proposal = DreamProposal.model_validate(
        proposal.model_dump()
    )

    rewrites = {
        item.filename: item
        for item in proposal.rewrites
    }
    deletes = {
        item.filename: item
        for item in proposal.deletes
    }

    if len(rewrites) != len(proposal.rewrites):
        raise ValueError("同一主题不能重复提交修改")

    if len(deletes) != len(proposal.deletes):
        raise ValueError("同一主题不能重复提交删除")

    if rewrites.keys() & deletes.keys():
        raise ValueError("同一主题不能同时修改和删除")

    conflict_files = {
        filename
        for conflict in proposal.conflicts
        for filename in conflict.filenames
    }

    existing = set(memory_store.list_filenames())
    required_sources = set(conflict_files)

    for item in proposal.rewrites:
        sources = set(item.source_files)

        if item.filename in existing:
            if item.filename not in sources:
                raise ValueError("更新主题必须引用其原文件")

        # 读取过的文件消失后，不能当作新主题重新创建。
        elif item.filename in read_versions:
            raise ValueError("目标主题已删除，需要重新整理")

        required_sources.update(sources)

    for item in proposal.deletes:
        sources = set(item.source_files)

        if item.filename not in sources:
            raise ValueError("删除主题必须引用其原文件")

        # 当前自动删除先支持有保留目标的合并操作。
        replacement = rewrites.get(item.replacement)

        if replacement is None:
            raise ValueError("删除必须有本次提交的合并目标")

        if item.filename not in replacement.source_files:
            raise ValueError("合并目标必须引用被删除主题")

        if replacement.filename not in sources:
            raise ValueError("删除操作必须引用合并目标")

        required_sources.update(sources)

    # 冲突主题也不能被其他操作间接用来生成新记忆。
    if conflict_files & (
        rewrites.keys() | deletes.keys()
    ):
        raise ValueError("冲突主题不能同时修改或删除")

    for item in [*proposal.rewrites, *proposal.deletes]:
        if conflict_files & set(item.source_files):
            raise ValueError("未解决冲突的主题不能参与整理")

    for filename in required_sources:
        expected = read_versions.get(filename)

        if expected is None:
            raise ValueError(f"必须先读取来源：{filename}")

        if filename not in fully_read:
            raise ValueError(f"必须完整读取来源正文：{filename}")

        if filename not in existing:
            raise ValueError(f"来源主题已删除：{filename}")

        current = memory_store.read(filename)

        if document_sha256(current) != expected:
            raise ValueError(
                f"来源主题已变化，需要重新整理：{filename}"
            )

    return proposal