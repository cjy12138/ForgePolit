from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_INDEX_ENTRIES = 200
MAX_INDEX_BYTES = 25 * 1024
TopicFilename = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}\.md$")]


class MemoryIndexChange(BaseModel):
    """索引收录变化，不修改或删除主题正文。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    add: list[TopicFilename] = Field(
        default_factory=list, max_length=MAX_INDEX_ENTRIES,
        description="加入 MEMORY.md 的主题，可以是本次新建或已有的主题。",
    )
    remove: list[TopicFilename] = Field(
        default_factory=list, max_length=MAX_INDEX_ENTRIES,
        description="仅移出 MEMORY.md，保留主题文件并允许搜索。",
    )

    @model_validator(mode="after")
    def check_names(self):
        if len(set(self.add)) != len(self.add) or len(set(self.remove)) != len(self.remove):
            raise ValueError("索引选择不能包含重复文件名")
        if set(self.add) & set(self.remove):
            raise ValueError("同一主题不能同时加入和移出索引")
        return self


class MemoryIndexState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filenames: list[TopicFilename] = Field(default_factory=list, max_length=MAX_INDEX_ENTRIES)

    @model_validator(mode="after")
    def check_unique(self):
        if len(set(self.filenames)) != len(self.filenames):
            raise ValueError("索引状态包含重复主题")
        return self
