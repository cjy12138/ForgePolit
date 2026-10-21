from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CheckpointRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    type: Literal["git_checkpoint"] = "git_checkpoint"

    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    session_id: str = Field(min_length=1)
    target_entry_id: str = Field(min_length=1)

    commit_id: str = Field(
        pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
    )
    base_commit_id: str = Field(
        pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
    )

    ref: str = Field(min_length=1)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @model_validator(mode="after")
    def check_ref(self):
        expected = f"refs/forgepilot/checkpoints/{self.id}"

        if self.ref != expected:
            raise ValueError("检查点引用与 ID 不一致")

        return self