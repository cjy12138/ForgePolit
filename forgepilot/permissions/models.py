from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


PermissionMode = Literal[
    "default",
    "accept_edits",
    "plan",
    "bypass",
    "auto",
    "bubble",
]

RuleAction = Literal["allow", "deny", "ask"]

CheckAction = Literal["allow", "deny", "abstain"]

DecisionAction = Literal["allow", "deny", "ask", "delegate"]

RuleSource = Literal[
    "policy",
    "user",
    "project",
    "local",
    "cli",
    "session",
]


# 数值越小，配置层级越高。
SOURCE_PRIORITY = {
    "policy": 0,
    "user": 1,
    "project": 2,
    "local": 3,
    "cli": 4,
    "session": 5,
}


class PermissionRule(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    action: RuleAction
    tool: str = Field(min_length=1)

    pattern: str | None = Field(
        default=None,
        min_length=1,
        description="可选的路径或命令匹配模式。",
    )
    operation_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="可选的操作指纹，用于当前会话的精确授权。",
    )


class RuleLayer(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    source: RuleSource
    rules: list[PermissionRule] = Field(default_factory=list)


class PermissionSettings(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    mode: PermissionMode = "default"

    layers: list[RuleLayer] = Field(default_factory=list)

    @field_validator("layers")
    @classmethod
    def check_sources(cls, layers):
        sources = [layer.source for layer in layers]

        if len(sources) != len(set(sources)):
            raise ValueError("同一配置来源不能重复")

        return layers


class ToolPermissionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any]
    workspace: str = Field(min_length=1)

    mode: PermissionMode = "default"

    task: str = Field(
        default="",
        description="当前用户任务，供审查器理解操作目的。",
    )


class ToolCheckResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    action: CheckAction
    reason: str = Field(min_length=1)


class PermissionDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    action: DecisionAction

    source: Literal[
        "boundary",
        "rule",
        "tool",
        "mode",
        "reviewer",
        "parent",
    ]

    reason: str = Field(min_length=1)

class ApprovalDecision(PermissionDecision):
    remember_session: bool = False


class ModeCheckResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    action: Literal[
        "allow",
        "deny",
        "ask",
        "review",
        "delegate",
    ]

    reason: str = Field(min_length=1)


class PermissionRoute(ModeCheckResult):
    source: Literal["rule", "tool", "mode"]

class SafetyReview(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    verdict: Literal["allow", "deny", "uncertain"]
    reason: str = Field(min_length=1, max_length=1000)

class PermissionRuleFile(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    rules: list[PermissionRule] = Field(
        default_factory=list
    )
