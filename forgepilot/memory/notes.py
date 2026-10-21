from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


NoteText = Annotated[
    str,
    Field(min_length=1, max_length=500),
]


class SessionNotes(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    goal: str = Field(
        min_length=1,
        max_length=1000,
        description="当前任务目标。",
    )

    constraints: list[NoteText] = Field(
        default_factory=list,
        max_length=20,
        description="当前任务必须遵守的限制。",
    )

    progress: list[NoteText] = Field(
        default_factory=list,
        max_length=20,
        description="已经完成和正在进行的工作。",
    )

    files: list[NoteText] = Field(
        default_factory=list,
        max_length=20,
        description="相关文件及其用途，不虚构文件。",
    )

    errors: list[NoteText] = Field(
        default_factory=list,
        max_length=20,
        description="尚未解决的错误或阻塞。",
    )

    verification: list[NoteText] = Field(
        default_factory=list,
        max_length=20,
        description="实际执行的验证及结果，未验证要明确说明。",
    )

    next_steps: list[NoteText] = Field(
        default_factory=list,
        max_length=20,
        description="接下来要做的工作。",
    )

    source_entry_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="支持笔记内容的原始消息节点 ID。",
    )


def render_session_notes(notes: SessionNotes) -> str:
    notes = SessionNotes.model_validate(
        notes.model_dump()
    )

    lines = [
        "# 会话工作笔记",
        "",
        "## 当前目标",
        "",
        " ".join(notes.goal.split()),
    ]

    sections = [
        ("任务限制", notes.constraints),
        ("工作进度", notes.progress),
        ("相关文件", notes.files),
        ("错误与阻塞", notes.errors),
        ("验证情况", notes.verification),
        ("下一步", notes.next_steps),
    ]

    for title, items in sections:
        lines.extend(["", f"## {title}", ""])

        if not items:
            lines.append("- 暂无记录")
            continue

        for item in items:
            text = " ".join(item.split())
            lines.append(f"- {text}")

    text = "\n".join(lines) + "\n"

    if len(text.encode("utf-8")) > 25 * 1024:
        raise ValueError("会话笔记超过 25 KiB")

    return text