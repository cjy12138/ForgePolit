from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from forgepilot.memory.index import MemoryIndexChange

MemoryType = Literal[
    "user",
    "feedback",
    "project",
    "reference",
]

class MemorySource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    source_session_id: str = Field(min_length=1)
    source_entry_ids: list[str] = Field(min_length=1)

class MemoryMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    name: str = Field(
        min_length=1,
        max_length=50,
        description="记忆主题名称",
    )

    type: MemoryType

    description: str = Field(
        min_length=1,
        max_length=150,
        description="索引中的简短描述，供召回模型判断相关性。",
    )

    source_session_id: str = Field(
        min_length=1,
        description="产生这条记忆的会话 ID。",
    )
    source_entry_ids: list[str] = Field(
        min_length=1,
        description="支持记忆内容的原始消息节点 ID。",
    )
    previous_sources: list[MemorySource] = Field(
        default_factory=list,
        description="之前版本的来源，更新时由程序保留。",
    )

class MemoryDocument(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    filename: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}\.md$",
        description="主题文件名，只允许小写字母、数字和下划线。",
    )

    metadata: MemoryMetadata

    content: str = Field(
        min_length=1,
        max_length=8000,
        description="Markdown 正文，保存可跨会话复用的信息。",
    )

class MemoryCandidate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    filename: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}\.md$",
        description="新增或更新的主题文件名。",
    )

    name: str = Field(
        min_length=1,
        max_length=50,
        description="记忆主题名称。",
    )

    type: MemoryType

    description: str = Field(
        min_length=1,
        max_length=150,
        description="简短描述，供后续召回判断相关性。",
    )

    content: str = Field(
        min_length=1,
        max_length=8000,
        description="可跨会话复用的 Markdown 正文。",
    )

    source_entry_ids: list[str] = Field(
        min_length=1,
        max_length=20,
        description="支持这条记忆的原始消息节点 ID。",
    )


class MemoryProposal(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    memories: list[MemoryCandidate] = Field(
        max_length=5,
        description="本轮提取的候选；没有可复用信息时返回空列表。",
    )
    index_change: MemoryIndexChange | None = Field(
        default=None,
        description="选择索引的 add/remove；空对象保持当前收录。省略时兼容旧候选，新增主题仅在索引有空间时自动收录。",
    )
