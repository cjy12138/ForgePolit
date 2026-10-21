import json
from copy import deepcopy
from typing import Any

from forgepilot.artifacts.store import ArtifactRef, ArtifactStore

def snip_tool_message(
    message: dict[str, Any],
    artifacts: ArtifactStore,
    *,
    head_chars: int = 2000,
    tail_chars: int = 1000,
) -> dict[str, Any]:
    for name, value in (
            ("head_chars", head_chars),
            ("tail_chars", tail_chars),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} 必须是非负整数")

    if head_chars + tail_chars == 0:
        raise ValueError("至少保留一个字符")

    result = deepcopy(message)

    if message.get("role") != "tool":
        return result

        # 已经缩减的视图不再重复加工。
    if message.get("snipped") is True:
        return result

    if "artifact" not in message:
        return result

    content = message.get("content")

    if not isinstance(content, str):
        raise ValueError("工具输出必须是字符串")

    if len(content) <= head_chars + tail_chars:
        return result

    ref = ArtifactRef.model_validate(message["artifact"])
    # 确保完整原文确实存在，且对应当前消息。
    original = artifacts.read_text(ref)

    if original != content:
        raise ValueError("产物内容与工具消息正文不一致")

    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars else ""
    omitted = len(content) - head_chars - tail_chars

    notice = {
        "type": "snipped_tool_output",
        "artifact": ref.model_dump(),
        "original_chars": len(content),
        "omitted_chars": omitted,
        "notice": (
            "以下仅为工具输出的首尾片段，完整输出已保存。"
            "未展示的部分可能包含重要信息；"
            "不能仅根据片段断言完整输出没有错误。"
        ),
    }

    preview = (
            json.dumps(notice, ensure_ascii=False)
            + "\n\n"
            + head
            + f"\n\n……省略 {omitted} 个字符……\n\n"
            + tail
    )

    # 元数据也有开销；没有缩短就保留原文。
    if len(preview.encode("utf-8")) >= len(content.encode("utf-8")):
        return result

    result["content"] = preview
    result["snipped"] = True

    return result