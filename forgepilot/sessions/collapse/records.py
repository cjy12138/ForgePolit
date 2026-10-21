from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.sessions.summarization.artifacts import (
    SummaryArtifactSource,
)
from forgepilot.sessions.summarization.schema import ContextSummary


class CollapseRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    type: Literal["collapse"] = "collapse"

    id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
    )

    session_id: str = Field(min_length=1)

    source_leaf_id: str = Field(
        min_length=1,
        description="发布记录时重新校验所依据的分支终点。",
    )
    prepared_leaf_id: str | None = Field(
        default=None,
        min_length=1,
        description="生成候选时所依据的原始分支终点。",
    )

    source_start_id: str = Field(
        min_length=1,
        description="折叠范围的第一个原始消息节点。",
    )

    source_end_id: str = Field(
        min_length=1,
        description="折叠范围的最后一个原始消息节点，包含该节点。",
    )

    source_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="折叠范围内原始消息的来源哈希。",
    )

    summary: ContextSummary

    artifact_sources: list[SummaryArtifactSource] = Field(
        default_factory=list,
        description="摘要涉及的完整工具输出来源。",
    )

    policy_version: Literal[1] = 1
    summary_version: Literal[1] = 1

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )