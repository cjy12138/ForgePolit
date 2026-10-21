import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class CircuitState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    session_id: str = Field(min_length=1)
    consecutive_failures: int = Field(default=0, ge=0)

class CircuitStateStore:
    def __init__(self, path: Path, session_id: str):
        self.path = path
        self.session_id = session_id

    def load(self) -> CircuitState:
        if not self.path.exists():
            return CircuitState(session_id=self.session_id)

        state = CircuitState.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

        if state.session_id != self.session_id:
            raise ValueError("熔断状态不属于当前会话")

        return state

    def save(self, consecutive_failures: int) -> None:
        state = CircuitState(
            session_id=self.session_id,
            consecutive_failures=consecutive_failures,
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)

        temporary = self.path.with_name(
            f"{self.path.name}.{uuid4().hex}.tmp"
        )

        try:
            with temporary.open(
                    "x",
                    encoding="utf-8",
                    newline="\n",
            ) as file:
                file.write(state.model_dump_json() + "\n")
                file.flush()
                os.fsync(file.fileno())

            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()