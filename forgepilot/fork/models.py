from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ForkTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    instruction: str = Field(min_length=1)
    agent_type: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$",
        description="可选的 Markdown 子 Agent 定义名称；指定后由定义文件配置基础工具。",
    )
    skills: list[str] = Field(default_factory=list, max_length=16,
                              description="启动前预加载的 Skill 名称；与子 Agent 定义中的 skills 合并。")
    base_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "list_files"],
        max_length=32,
        description="子任务的基础工具名称；只能使用程序允许的已注册工具，不允许 fork_tasks。",
    )
    # None 表示继承主任务；实际权限校验在派发时进行。
    permission_mode: Literal["default", "accept_edits", "plan", "bypass", "auto"] | None = None

    @model_validator(mode="after")
    def check_base_tools(self):
        if any(not name.strip() for name in self.skills) or len(set(self.skills)) != len(self.skills):
            raise ValueError("预加载 Skill 名称不能为空或重复")
        if any(not name.strip() for name in self.base_tools):
            raise ValueError("基础工具名称不能为空")
        if len(set(self.base_tools)) != len(self.base_tools):
            raise ValueError("基础工具不能重复")
        if "fork_tasks" in self.base_tools:
            raise ValueError("子任务不允许递归派发")
        return self


class ForkTaskState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    task: ForkTaskInput
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    status: Literal["pending", "running", "completed", "failed", "interrupted"] = "pending"
    summary: str | None = None
    error: str | None = None
    model_requests: int = Field(default=0, ge=0)
    request_limit: int = Field(default=32, ge=1)


class ForkTasksInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tasks: list[ForkTaskInput] = Field(
        min_length=1, max_length=1,
        description="当前版本一次仅允许一个子任务；权限模式省略时继承主任务，不能自行提升权限。",
    )

    @model_validator(mode="after")
    def unique_names(self):
        if len({task.name for task in self.tasks}) != len(self.tasks):
            raise ValueError("子任务名称不能重复")
        return self


class ForkJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    parent_session_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    tasks: list[ForkTaskState] = Field(min_length=1, max_length=1)
