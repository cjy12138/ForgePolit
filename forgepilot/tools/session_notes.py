import json
from functools import partial

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.contracts import ToolContext
from forgepilot.memory.notes import render_session_notes
from forgepilot.memory.notes_store import SessionNotesStore


class ReadSessionNotesInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    offset: int = Field(
        default=0,
        ge=0,
        description="笔记正文起始字符位置，从 0 开始。",
    )

    limit: int = Field(
        default=2000,
        ge=1,
        le=8000,
        description="最多返回的字符数。",
    )


def read_session_notes(
    *,
    context: ToolContext,
    notes_store: SessionNotesStore,
    offset: int = 0,
    limit: int = 2000,
) -> str:
    params = ReadSessionNotesInput(
        offset=offset,
        limit=limit,
    )

    state = notes_store.get_current()

    if state is None:
        raise ValueError("当前分支没有可用的会话笔记")

    content = render_session_notes(state.notes)

    if params.offset > len(content):
        raise ValueError("offset 超出笔记长度")

    end = min(
        params.offset + params.limit,
        len(content),
    )

    return json.dumps(
        {
            "session_id": state.session_id,
            "source_leaf_id": state.source_leaf_id,
            "history_tokens": state.history_tokens,
            "source_entry_ids": state.notes.source_entry_ids,
            "offset": params.offset,
            "end": end,
            "total_chars": len(content),
            "next_offset": (
                end if end < len(content) else None
            ),
            "content": content[params.offset:end],
        },
        ensure_ascii=False,
    )


def build_session_notes_tools(
    *,
    notes_store: SessionNotesStore,
):
    return {
        "read_session_notes": {
            "name": "read_session_notes",
            "description": (
                "分段读取当前会话、当前分支可用的工作笔记。"
                "用于恢复任务目标、进度、验证情况和下一步。"
                "笔记只反映来源节点当时的状态，可能落后于新消息；"
                "不能代替检查当前代码或实际验证。"
            ),
            "input_model": ReadSessionNotesInput,
            "handler": partial(
                read_session_notes,
                notes_store=notes_store,
            ),
        }
    }