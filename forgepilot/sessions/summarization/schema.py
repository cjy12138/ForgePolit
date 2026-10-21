from __future__ import annotations

from typing import Annotated, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

if TYPE_CHECKING:
    from forgepilot.sessions.models import MessageEntry

NonEmptyText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
    ),
]

class SummaryItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    text: NonEmptyText = Field(
        description="根据来源记录总结的信息，不得虚构。",
    )

    source_entry_ids: list[NonEmptyText] = Field(
        min_length=1,
        description="支持该信息的原始消息节点 ID，至少提供一个。",
    )

class ContextSummary(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    goal: list[SummaryItem] = Field(
        description="来源中明确出现的任务目标；未出现时为空列表。",
    )

    constraints: list[SummaryItem] = Field(
        description="用户要求和限制，例如允许修改的文件、禁止事项。",
    )

    completed: list[SummaryItem] = Field(
        description="有来源支持的已完成事项，不把计划写成完成。",
    )

    in_progress: list[SummaryItem] = Field(
        description="正在处理但尚未完成的事项。",
    )

    blockers: list[SummaryItem] = Field(
        description="阻碍继续执行的问题，以及执行情况未知的事项。",
    )

    decisions: list[SummaryItem] = Field(
        description="已作出的关键决定及其理由。",
    )

    next_steps: list[SummaryItem] = Field(
        description="来源中已有的后续计划，不新增未经要求的任务。",
    )

    verification: list[SummaryItem] = Field(
        description=(
            "实际观察到的测试命令、结果及适用代码版本；"
            "无法确定版本时明确说明。"
        ),
    )

    critical_context: list[SummaryItem] = Field(
        description="继续任务需要的路径、函数名、错误信息等。",
    )

def validate_summary_sources(
    summary: ContextSummary,
    entries: list[MessageEntry],
) -> None:
    allowed_ids = {entry.id for entry in entries}

    for field_name in ContextSummary.model_fields:
        items = getattr(summary, field_name)

        for item in items:
            unknown_ids = (
                set(item.source_entry_ids) - allowed_ids
            )

            if unknown_ids:
                raise ValueError(
                    f"{field_name} 引用了摘要范围外的节点："
                    + ", ".join(sorted(unknown_ids))
                )
