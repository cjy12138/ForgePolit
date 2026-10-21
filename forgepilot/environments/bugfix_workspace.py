import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from forgepilot.checkpoints.git import GitCheckpointStore


@dataclass(frozen=True)
class BugfixWorkspace:
    id: str
    path: Path
    source_workspace: Path
    baseline_commit_id: str


class BugfixWorkspaceManager:
    def __init__(self, source: Path, tasks_root: Path):
        self.source = source.resolve()
        self.tasks_root = tasks_root.resolve()
        self.checkpoints = GitCheckpointStore(self.source)

        if self.tasks_root.is_relative_to(self.source):
            raise ValueError("任务目录必须放在原仓库之外")

    def _git(self, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.source), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or "Git 操作失败"
            )

        return result.stdout.strip()

    def create(self) -> BugfixWorkspace:
        self.tasks_root.mkdir(parents=True, exist_ok=True)

        checkpoint = self.checkpoints.create()
        task_id = uuid4().hex
        task_path = self.tasks_root / task_id

        self._git([
            "worktree",
            "add",
            "--detach",
            str(task_path),
            checkpoint.commit_id,
        ])

        return BugfixWorkspace(
            id=task_id,
            path=task_path,
            source_workspace=self.source,
            baseline_commit_id=checkpoint.commit_id,
        )

    def remove(self, task: BugfixWorkspace) -> None:
        expected_path = self.tasks_root / task.id

        if (
            task.source_workspace != self.source
            or task.path != expected_path
        ):
            raise ValueError("任务工作区不属于当前管理器")

        # 不强制删除：存在未保存的修复时，Git 会拒绝。
        self._git([
            "worktree",
            "remove",
            str(task.path),
        ])