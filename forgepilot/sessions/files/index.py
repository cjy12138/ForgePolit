from dataclasses import dataclass

from forgepilot.sessions.models import MessageEntry, SessionNode
from forgepilot.sessions.validation import require_complete_tools

@dataclass(frozen=True)
class FileContextCandidate:
    path: str
    tool_name: str
    call_entry_id: str
    result_entry_id: str

def find_file_context_candidates(
    branch: list[SessionNode],
    *,
    max_files: int = 4,
) -> list[FileContextCandidate]:
    if type(max_files) is not int or max_files < 1:
        raise TypeError("max_files 必须是正整数")

    require_complete_tools(branch)

    pending = {}
    latest_by_path = {}

    for entry in branch:
        if not isinstance(entry, MessageEntry):
            continue

        message = entry.payload
        role = message.get("role")

        if role == "assistant":
            kind = message.get("type")

            if kind == "tool_call":
                calls = [message]
            elif kind == "tool_batch":
                calls = message["calls"]
            else:
                continue

            for call in calls:
                pending[call["id"]] = (entry.id,call)

        elif role == "tool":
            call_id = message["tool_call_id"]
            call_entry_id, call = pending.pop(call_id)

            if message.get("is_error") is not False:
                continue

            name = call["name"]

            if name not in {"read_file", "edit_file"}:
                continue

            path = call["arguments"].get("path")

            if not isinstance(path, str) or not path.strip():
                continue

            candidate = FileContextCandidate(
                path=path,
                tool_name=name,
                call_entry_id=call_entry_id,
                result_entry_id=entry.id,
            )

            # 同一路径更新到最近一次成功交互。
            latest_by_path.pop(path, None)
            latest_by_path[path] = candidate

    # 最近操作的文件优先。
    return list(reversed(list(latest_by_path.values())))[:max_files]
