import json
from math import ceil
from typing import Any

def estimate_request_tokens(
    request: dict[str, Any],
) -> int:
    # 只统计作为模型上下文的部分。
    content = {
        "messages": request["messages"],
        "tools": request.get("tools", []),
    }

    text = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # 教学版粗估：每 3 个 UTF-8 字节按 1 Token 计算。
    # 不是模型的真实 tokenizer，也不保证不会低估。
    content_tokens = ceil(len(text.encode("utf-8")) / 3)

    # 为消息边界及协议模板增加粗略开销。
    protocol_tokens = (
        32
        + 8 * len(content["messages"])
        + 16 * len(content["tools"])
    )

    return content_tokens + protocol_tokens