import json
from functools import partial

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.artifacts.store import ArtifactRef, ArtifactStore
from forgepilot.contracts import ToolContext
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore

class ReadArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_id: str = Field(
        pattern=r"^[0-9a-f]{32}$",
        description="工具输出中提供的产物 ID。",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="起始字符位置，从 0 开始。",
    )
    limit: int = Field(
        default=2000,
        ge=1,
        le=8000,
        description="最多返回的字符数。",
    )

def read_artifact(
    *,
    artifact_id: str,
    context: ToolContext,
    session_store: SessionStore,
    artifact_store: ArtifactStore,
    offset: int = 0,
    limit: int = 2000,
) -> str:
    # 直接调用函数时也执行参数校验。
    params = ReadArtifactInput(
        artifact_id=artifact_id,
        offset=offset,
        limit=limit,
    )
    ref = None

    # 查原始分支，而非压缩后的模型视图。
    # 已被摘要覆盖的工具结果仍可作为引用来源。
    for entry in session_store.get_current_branch():
        if not isinstance(entry, MessageEntry):
            continue

        message = entry.payload

        if message.get("role") != "tool":
            continue

        raw_ref = message.get("artifact")

        if not isinstance(raw_ref, dict):
            continue

        if raw_ref.get("id") != params.artifact_id:
            continue

        candidate = ArtifactRef.model_validate(raw_ref)

        if ref is not None and candidate != ref:
            raise ValueError("同一产物 ID 存在冲突引用")

        ref = candidate

    if ref is None:
        raise ValueError("当前分支没有引用此产物")

    content = artifact_store.read_text(ref)

    if params.offset > len(content):
        raise ValueError("offset 超出产物长度")

    end = min(params.offset + params.limit, len(content))

    return json.dumps(
        {
            "artifact_id": ref.id,
            "offset": params.offset,
            "end": end,
            "total_chars": len(content),
            "next_offset": end if end < len(content) else None,
            "content": content[params.offset:end],
        },
        ensure_ascii=False,
    )

def build_artifact_tools(
    *,
    session_store: SessionStore,
    artifact_store: ArtifactStore,
):
    return {
        "read_artifact": {
            "name": "read_artifact",
            "description": (
                "分段读取当前会话分支引用的历史工具输出。"
                "这是当时保存的输出，不代表当前文件内容。"
                "offset 和 limit 均按字符计数。"
            ),
            "input_model": ReadArtifactInput,
            "handler": partial(
                read_artifact,
                session_store=session_store,
                artifact_store=artifact_store,
            ),
        },
    }