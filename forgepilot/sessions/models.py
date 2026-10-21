from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.snip.records import SnipRecord
from forgepilot.sessions.summarization.artifacts import SummaryArtifactSource
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.checkpoints.records import CheckpointRecord

def new_id() -> str:
    return uuid4().hex

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class MessageEntry(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    id:str = Field(
        default_factory=new_id,
        min_length=1,
    )
    session_id:str = Field(min_length=1)
    parent_id:str|None = Field(
        default=None,
        min_length=1,
    )
    type: Literal["message"] = "message"
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)

class SessionHeader(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    type: Literal["session_header"] = "session_header"
    schema_version: Literal[1] = 1
    session_id: str = Field(default_factory=new_id, min_length=1)
    project_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

class CursorChanged(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    type: Literal["cursor_changed"] = "cursor_changed"
    session_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

class CompactionEntry(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    type: Literal["compaction"] = "compaction"

    id: str = Field(
        default_factory=new_id,
        min_length=1,
    )

    session_id: str = Field(min_length=1)

    parent_id: str = Field(
        min_length=1,
        description="压缩节点接在已总结来源分支的末尾。",
    )

    source_leaf_id: str = Field(
        min_length=1,
        description="生成摘要时所依据的分支终点。",
    )

    first_kept_entry_id: str = Field(
        min_length=1,
        description="继续保留原文的起始消息节点。",
    )

    summary: ContextSummary

    artifact_sources: list[SummaryArtifactSource] = Field(
        default_factory=list,
        description="程序维护的完整工具输出来源。",
    )

    summary_version: Literal[1] = 1

    artifact_sources_version: Literal[1, 2] = 1

    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_boundary(self):
        if self.parent_id != self.source_leaf_id:
            raise ValueError(
                "压缩节点必须接在生成摘要时的来源终点之后"
            )

        if self.id in {
            self.parent_id,
            self.first_kept_entry_id,
        }:
            raise ValueError("压缩节点不能引用自身作为历史边界")

        return self


SessionNode = MessageEntry | CompactionEntry


SessionRecord = Annotated[
    MessageEntry
    | CompactionEntry
    | CursorChanged
    | SnipRecord
    | CollapseRecord
    | CheckpointRecord,
    Field(discriminator="type"),
]

record_adapter = TypeAdapter(SessionRecord)
