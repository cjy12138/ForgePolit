from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pathlib import Path

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    exit_code: int | None = None

class FinalResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    type: Literal["final"]
    content: str = Field(min_length=1)

class ToolCallResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    type: Literal["tool_call"]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ToolBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["tool_batch"]
    calls: list[ToolCallResponse] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def check_unique_ids(self):
        ids = [call.id for call in self.calls]
        if len(ids) != len(set(ids)):
            raise ValueError("同一批工具调用的 id 不能重复")
        return self


ModelResponse = Annotated[
    FinalResponse | ToolCallResponse | ToolBatchResponse,
    Field(discriminator="type"),
]

response_adapter = TypeAdapter(ModelResponse)

@dataclass(frozen=True)
class ToolContext:
    workspace: Path
    task: str = ""
    tool_call_id: str | None = None
