from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from forgepilot.skills.service import PreparedSkill
from forgepilot.tool_registry import ToolRegistry


class SkillMessage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    role: Literal["system"]
    content: str


class SkillTaskState(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    session_id: str
    name: str | None
    tools: list[str]
    messages: list[SkillMessage]


def save_skill_state(
    path: Path,
    *,
    session_id: str,
    prepared: PreparedSkill,
) -> None:
    state = SkillTaskState(
        session_id=session_id,
        name=prepared.name,
        tools=[
            item["name"]
            for item in prepared.registry.definitions()
        ],
        messages=[
            SkillMessage.model_validate(message)
            for message in prepared.messages
        ],
    )

    temporary = path.with_suffix(".tmp")

    try:
        temporary.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_skill_state(
    path: Path,
    *,
    session_id: str,
    tool_pool: ToolRegistry,
) -> PreparedSkill:
    state = SkillTaskState.model_validate_json(
        path.read_text(encoding="utf-8")
    )

    if state.session_id != session_id:
        raise ValueError("Skill 状态不属于当前会话")

    registry = tool_pool.select_tools(
        set(state.tools)
    )

    return PreparedSkill(
        name=state.name,
        messages=[
            message.model_dump()
            for message in state.messages
        ],
        registry=registry,
    )