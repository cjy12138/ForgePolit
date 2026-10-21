import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.environments.bugfix_workspace import (
    BugfixWorkspaceManager,
)


def git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def main():
    with TemporaryDirectory(prefix="forgepilot-worktree-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()

        original = source / "calculator.py"
        original.write_text(
            "def add(a, b):\n    return a - b\n",
            encoding="utf-8",
        )

        git(source, "init")
        git(source, "add", "calculator.py")
        git(
            source,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial",
        )

        # 模拟用户尚未提交的修改。
        current_code = "def add(a, b):\n    return a - b  # TODO\n"
        original.write_text(current_code, encoding="utf-8")

        manager = BugfixWorkspaceManager(
            source=source,
            tasks_root=root / "tasks",
        )

        task = manager.create()
        task_file = task.path / "calculator.py"

        assert task_file.read_text(encoding="utf-8") == current_code
        print("任务副本包含原工作区的未提交修改：通过")

        task_file.write_text(
            "def add(a, b):\n    return a + b\n",
            encoding="utf-8",
        )

        assert original.read_text(encoding="utf-8") == current_code
        print("修改任务副本不影响原文件：通过")

        try:
            manager.remove(task)
        except RuntimeError:
            assert task.path.exists()
            print("拒绝删除存在未提交修复的任务副本：通过")
        else:
            raise AssertionError("不应直接删除未提交修复")

        # 仅为测试清理：撤回刚刚在临时副本中的修改。
        task_file.write_text(current_code, encoding="utf-8")
        manager.remove(task)

        assert not task.path.exists()
        print("干净任务副本可正常清理：通过")


if __name__ == "__main__":
    main()