"""读取已有任务；恢复只使用登记的原副本，不创建新基线。"""
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from forgepilot.environments.bugfix_workspace import BugfixWorkspace


@dataclass(frozen=True)
class SavedBugfixTask:
    task: BugfixWorkspace
    ticket: str
    image: str
    run_dir: Path


def load_bugfix_task(tasks_root: Path, task_id: str) -> SavedBugfixTask:
    if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("任务 ID 必须是 32 位小写十六进制字符串")
    tasks_root = tasks_root.resolve()
    run_dir = tasks_root / "_runs" / task_id
    data = json.loads((run_dir / "task.json").read_text(encoding="utf-8"))
    if data.get("task_id") != task_id:
        raise ValueError("任务信息与任务 ID 不一致")
    for key in ("source_workspace", "task_workspace", "ticket", "image"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"任务信息缺少 {key}")
    baseline = data.get("baseline_commit_id")
    if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", baseline):
        raise ValueError("任务基线提交 ID 不合法")
    source = Path(data["source_workspace"]).resolve()
    workspace = Path(data["task_workspace"]).resolve()
    expected = tasks_root / task_id
    if workspace != expected or expected.is_symlink() or tasks_root.is_relative_to(source):
        raise ValueError("任务副本路径不属于当前任务目录")
    if not workspace.is_dir() or not source.is_dir():
        raise ValueError("原仓库或任务副本已不存在")

    def git(path, *args):
        result = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "任务 Git 信息无法读取")
        return result.stdout.strip()

    if Path(git(workspace, "rev-parse", "--show-toplevel")).resolve() != workspace:
        raise ValueError("任务副本不是 Git 工作区根目录")
    source_git = git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")
    task_git = git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(source_git).resolve() != Path(task_git).resolve():
        raise ValueError("任务副本不属于登记的原仓库")
    git(workspace, "cat-file", "-e", f"{baseline}^{{commit}}")
    return SavedBugfixTask(
        task=BugfixWorkspace(task_id, workspace, source, baseline),
        ticket=data["ticket"], image=data["image"], run_dir=run_dir,
    )
