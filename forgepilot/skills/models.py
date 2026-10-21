from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

MAX_SKILL_BYTES = 64 * 1024




class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_\-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    tools: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("tools")
    @classmethod
    def check_tools(cls, values):
        if any(not value or value != value.strip() for value in values):
            raise ValueError("工具名称必须是非空且无首尾空格的字符串")
        if len(set(values)) != len(values):
            raise ValueError("工具依赖不能重复")
        return values


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    metadata: SkillMetadata
    content: str = Field(min_length=1)


def parse_skill(text: str) -> SkillDocument:
    if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
        raise ValueError("SKILL.md 不能超过 64 KiB")
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md 必须以 YAML frontmatter 开始")
    end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if end is None:
        raise ValueError("frontmatter 缺少结束分隔符")
    header = "\n".join(lines[1:end])
    if len(header.encode("utf-8")) > 4096:
        raise ValueError("frontmatter 不能超过 4 KiB")
    try:
        data = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        raise ValueError("frontmatter 不是合法 YAML") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter 必须是字段映射")
    return SkillDocument(
        metadata=SkillMetadata.model_validate(data),
        content="\n".join(lines[end + 1:]),
    )


def render_skill(document: SkillDocument) -> str:
    header = yaml.safe_dump(document.metadata.model_dump(), allow_unicode=True, sort_keys=False)
    return f"---\n{header}---\n\n{document.content}\n"
