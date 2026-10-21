import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forgepilot.permissions.models import PermissionRule, ToolPermissionRequest
from forgepilot.permissions.operation import hash_permission_operation


def normalize_workspace(workspace: Path | str) -> str:
    return os.path.normcase(str(Path(workspace).resolve()))


class SessionPermissionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    rules: list[PermissionRule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def check_exact_grants(cls, rules):
        for rule in rules:
            if rule.action != "allow" or rule.operation_sha256 is None or rule.pattern is not None:
                raise ValueError("会话状态只能保存具有操作指纹的精确允许规则")
        return rules


class SessionPermissionState:
    def __init__(self, path: Path, *, session_id: str, workspace: Path):
        self.path = Path(path)
        self.session_id = session_id
        self.workspace = normalize_workspace(workspace)

    def load(self) -> list[PermissionRule]:
        if not self.path.exists():
            return []
        document = SessionPermissionDocument.model_validate_json(
            self.path.read_text(encoding="utf-8-sig")
        )
        if document.session_id != self.session_id or document.workspace != self.workspace:
            raise ValueError("权限授权状态不属于当前会话或工作区")
        return document.rules

    def add(self, request: ToolPermissionRequest) -> list[PermissionRule]:
        if normalize_workspace(request.workspace) != self.workspace:
            raise ValueError("不能把其他工作区的批准保存到当前会话")
        grant = PermissionRule(
            action="allow", tool=request.tool_name,
            operation_sha256=hash_permission_operation(request),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=5):
            rules = self.load()
            if grant in rules:
                return rules
            rules.append(grant)
            document = SessionPermissionDocument(
                session_id=self.session_id, workspace=self.workspace, rules=rules,
            )
            temporary = None
            try:
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
                ) as file:
                    temporary = Path(file.name)
                    file.write(document.model_dump_json(indent=2) + "\n")
                os.replace(temporary, self.path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        return rules
