import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

def hash_tool_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()

class SnipRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )

    type: Literal["snip"] = "snip"
    schema_version: Literal[1] = 1

    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str = Field(min_length=1)

    source_leaf_id: str = Field(
        min_length=1,
        description="决定采用 Snip 时的分支终点。",
    )

    target_entry_id: str = Field(
        min_length=1,
        description="被截断展示的工具结果节点。",
    )

    source_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="原始工具结果 payload 的哈希。",
    )

    policy_version: Literal[1] = 1
    head_chars: int = Field(default=2000, ge=0)
    tail_chars: int = Field(default=1000, ge=0)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_preview_size(self):
        if self.head_chars + self.tail_chars == 0:
            raise ValueError("至少保留一个字符")

        return self