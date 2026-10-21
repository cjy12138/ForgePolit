from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TaskMode = Literal["coding", "bugfix"]

class TaskRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    text: str = Field(
        min_length=1,
        description="用户提交的任务描述。",
    )
    mode: TaskMode | None = Field(
        default=None,
        description="用户明确指定的模式；未指定时为 None。",
    )

class TaskIntent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    goal: str = Field(
        min_length=1,
        description="理解后的任务目标，不得添加用户未要求的工作。",
    )
    suggested_mode: TaskMode = Field(
        description="模型建议使用的执行模式。",
    )
    clarification_question: str = Field(
        description="缺少关键任务信息时提出的问题；无需澄清时为空字符串。",
    )

class RouteDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    status: Literal["ready", "needs_input"]
    mode: TaskMode
    goal: str
    question: str | None = None

def route_task(
    request: TaskRequest,
    intent: TaskIntent,
) -> RouteDecision:
    selected_mode = (
        request.mode
        if request.mode is not None
        else intent.suggested_mode
    )

    if intent.clarification_question:
        return RouteDecision(
            status="needs_input",
            mode=selected_mode,
            goal=intent.goal,
            question=intent.clarification_question,
        )

    return RouteDecision(
        status="ready",
        mode=selected_mode,
        goal=intent.goal,
    )