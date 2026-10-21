from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.memory.models import MemoryType
from forgepilot.memory.index import MemoryIndexChange


TopicFilename = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]{0,63}\.md$"),
]


class DreamModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class DreamRewrite(DreamModel):
    filename: TopicFilename

    name: str = Field(min_length=1, max_length=50)
    type: MemoryType
    description: str = Field(min_length=1, max_length=150)
    content: str = Field(min_length=1, max_length=8000)

    source_files: list[TopicFilename] = Field(
        min_length=1,
        max_length=20,
        description="本次整理依据的已有记忆文件，必须先读取正文。",
    )


class DreamDelete(DreamModel):
    filename: TopicFilename

    reason: str = Field(min_length=1, max_length=500)

    replacement: TopicFilename | None = Field(
        default=None,
        description="合并后的保留文件；删除重复主题时必须指定。",
    )

    source_files: list[TopicFilename] = Field(
        min_length=1,
        max_length=20,
        description="支持删除判断的已读取记忆文件。",
    )


class DreamConflict(DreamModel):
    filenames: list[TopicFilename] = Field(
        min_length=2,
        max_length=10,
    )

    question: str = Field(
        min_length=1,
        max_length=500,
        description="无法确定哪个说法有效，需要用户确认的问题。",
    )


class DreamProposal(DreamModel):
    index_change: MemoryIndexChange | None = Field(
        default=None,
        description="调整 MEMORY.md 收录的主题。移出索引不删除正文；空对象保持选择。",
    )
    rewrites: list[DreamRewrite] = Field(
        default_factory=list,
        max_length=10,
    )

    deletes: list[DreamDelete] = Field(
        default_factory=list,
        max_length=10,
    )

    conflicts: list[DreamConflict] = Field(
        default_factory=list,
        max_length=10,
    )
