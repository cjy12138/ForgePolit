from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1)
    tools: list[str] | None = None
    disallowedTools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    prompt: str = ""

    @field_validator("tools", "disallowedTools", "skills", mode="before")
    @classmethod
    def parse_names(cls, value):
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if isinstance(value, list):
            if any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError("配置名称必须是非空字符串")
            if len(value) != len(set(value)):
                raise ValueError("配置名称不能重复")
        return value


class AgentDefinitionStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def read(self, name: str) -> AgentDefinition:
        AgentDefinition(name=name, description="名称校验")
        path = self.root / f"{name}.md"
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("子 Agent 定义路径不合法")
        if path.stat().st_size > 64 * 1024:
            raise ValueError("子 Agent 定义不能超过 64 KiB")
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if not lines or lines[0] != "---":
            raise ValueError("子 Agent 定义缺少 frontmatter")
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
        if end is None:
            raise ValueError("frontmatter 未结束")
        data = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(data, dict) or "prompt" in data:
            raise ValueError("提示正文应放在 frontmatter 之后")
        definition = AgentDefinition.model_validate({**data, "prompt": "\n".join(lines[end + 1:]).strip()})
        if definition.name != name:
            raise ValueError("子 Agent 名称与文件名不一致")
        return definition

    def for_session(self, name: str, session_path: Path) -> AgentDefinition:
        path = session_path.parent / "agent-definition.json"
        with FileLock(str(path) + ".lock", timeout=5):
            if path.exists():
                definition = AgentDefinition.model_validate_json(path.read_text(encoding="utf-8"))
                if definition.name != name:
                    raise ValueError("保存的子 Agent 定义与任务类型不一致")
                return definition
            definition = self.read(name)
            temporary = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                        suffix=".tmp", delete=False) as file:
                    temporary = Path(file.name)
                    file.write(definition.model_dump_json(indent=2))
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return definition
