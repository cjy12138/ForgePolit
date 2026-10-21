from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock

from forgepilot.skills.models import (
    MAX_SKILL_BYTES, SkillDocument, SkillMetadata, parse_skill, render_skill,
)


# 导入阶段只检查名称，实际启用还会核对运行时工具池和权限。
from forgepilot.skills.catalog import KNOWN_TOOL_NAMES


class SkillStore:
    def __init__(self, root: Path, *, known_tools=KNOWN_TOOL_NAMES):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.known_tools = frozenset(known_tools)
        self.lock = FileLock(str(self.root / ".skills.lock"))

    def _path(self, name: str) -> Path:
        # 复用名称规则，拒绝路径片段。
        SkillMetadata(name=name, description="路径校验")
        directory = self.root / name
        target = directory / "SKILL.md"
        if directory.is_symlink() or target.is_symlink():
            raise ValueError("Skill 目录或文件不能是符号链接")
        if not target.resolve().is_relative_to(self.root):
            raise ValueError("Skill 路径不在项目 Skill 目录内")
        return target

    def _validate(self, document: SkillDocument) -> SkillDocument:
        document = SkillDocument.model_validate(document.model_dump())
        unknown = set(document.metadata.tools) - self.known_tools
        if unknown:
            raise ValueError(f"Skill 声明了未知工具：{', '.join(sorted(unknown))}")
        return document

    def read(self, name: str) -> SkillDocument:
        path = self._path(name)
        with path.open("rb") as file:
            data = file.read(MAX_SKILL_BYTES + 1)
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("SKILL.md 不能超过 64 KiB")
        document = self._validate(parse_skill(data.decode("utf-8-sig")))
        if document.metadata.name != name:
            raise ValueError("Skill 名称与目录名不一致")
        return document

    def save(self, document: SkillDocument, *, replace: bool = False) -> Path:
        document = self._validate(document)
        text = render_skill(document)
        if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
            raise ValueError("保存后的 SKILL.md 不能超过 64 KiB")
        with self.lock:
            target = self._path(document.metadata.name)
            if target.exists() and not replace:
                raise ValueError("同名 Skill 已存在；更新时显式指定 --replace")
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent,
                                    prefix=".skill-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(text)
            try:
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return target

    def import_file(self, source: Path, *, replace: bool = False) -> Path:
        with source.open("rb") as file:
            data = file.read(MAX_SKILL_BYTES + 1)
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("导入文件不能超过 64 KiB")
        return self.save(parse_skill(data.decode("utf-8-sig")), replace=replace)

    def list_metadata(self) -> list[SkillMetadata]:
        return [self.read(path.name).metadata
                for path in sorted(self.root.iterdir()) if path.is_dir()]
