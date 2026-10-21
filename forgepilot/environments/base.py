from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass(frozen=True)
class ExecutionResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class Environment(Protocol):
    def execute(
        self,
        *,
        argv: list[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        ...