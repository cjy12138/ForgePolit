import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock

from forgepilot.permissions.config import permission_file_paths, read_rule_file
from forgepilot.permissions.models import PermissionRule, PermissionRuleFile


WRITABLE_SOURCES = {"user", "project", "local"}


class PermissionRuleStore:
    def __init__(self, *, workspace: Path, policy_path=None, user_path=None):
        self.paths = permission_file_paths(
            workspace=workspace, policy_path=policy_path, user_path=user_path,
        )

    def path_for(self, source: str, *, writing=False) -> Path:
        if source not in self.paths:
            raise ValueError("该来源不是文件配置，不能通过此 CLI 管理")
        if writing and source not in WRITABLE_SOURCES:
            raise ValueError("系统策略只允许查看，不能通过此 CLI 修改")
        return self.paths[source]

    def list_rules(self, source: str) -> list[PermissionRule]:
        return read_rule_file(self.path_for(source))

    def _write(self, path: Path, rules: list[PermissionRule]) -> None:
        document = PermissionRuleFile(rules=rules)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(document.model_dump_json(indent=2) + "\n")
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def add(self, source: str, rule: PermissionRule) -> tuple[int, bool]:
        path = self.path_for(source, writing=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock", timeout=5):
            rules = read_rule_file(path)
            if rule in rules:
                return rules.index(rule) + 1, False
            rules.append(rule)
            self._write(path, rules)
            return len(rules), True

    def remove(self, source: str, index: int) -> PermissionRule:
        path = self.path_for(source, writing=True)
        if not path.parent.is_dir():
            raise ValueError("该来源没有规则可删除")
        with FileLock(str(path) + ".lock", timeout=5):
            rules = read_rule_file(path)
            if type(index) is not int or not 1 <= index <= len(rules):
                raise ValueError("规则序号不存在，请先 list 查看")
            removed = rules.pop(index - 1)
            self._write(path, rules)
            return removed
