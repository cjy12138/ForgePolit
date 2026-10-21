from dataclasses import dataclass

from forgepilot.artifacts.store import ArtifactRef, ArtifactStore
from forgepilot.sessions.models import CompactionEntry, MessageEntry
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import require_complete_tools

@dataclass(frozen=True)
class SessionIntegrity:
    session_id: str
    leaf_id: str | None
    message_count: int
    compaction_count: int
    artifact_count: int

def check_session_integrity(
    store: SessionStore,
    artifacts: ArtifactStore,
) -> SessionIntegrity:
    # 重新从文件解析，检查记录及压缩节点是否合法。
    header, _, leaf_id = store.replay()
    branch = store.get_current_branch()

    require_complete_tools(branch)

    message_count = 0
    compaction_count = 0
    checked_artifacts = {}

    for node in branch:
        if isinstance(node, CompactionEntry):
            compaction_count += 1
            continue

        if not isinstance(node, MessageEntry):
            continue

        message_count += 1
        message = node.payload

        if message.get("role") != "tool":
            continue

        raw_ref = message.get("artifact")

        # 兼容早期没有保存产物的历史。
        if raw_ref is None:
            continue

        ref = ArtifactRef.model_validate(raw_ref)

        if ref.id in checked_artifacts:
            previous_ref, content = checked_artifacts[ref.id]

            if previous_ref != ref:
                raise ValueError("同一产物 ID 存在冲突引用")
        else:
            content = artifacts.read_text(ref)
            checked_artifacts[ref.id] = (ref, content)

        if content != message.get("content"):
            raise ValueError("完整产物与会话中的原始工具正文不一致")

    return SessionIntegrity(
        session_id=header.session_id,
        leaf_id=leaf_id,
        message_count=message_count,
        compaction_count=compaction_count,
        artifact_count=len(checked_artifacts),
    )
