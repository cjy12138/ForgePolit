
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.checkpoints.git import GitCheckpointStore


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        git(root, "init")
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@local.invalid")

        (root / ".gitignore").write_text(
            ".env\n.forgepilot/\n",
            encoding="utf-8",
        )
        code = root / "calculator.py"
        code.write_text(
            "def add(a, b):\n    return a - b\n",
            encoding="utf-8",
        )

        git(root, "add", ".")
        git(root, "commit", "-m", "initial")

        # 暂存版本与工作区版本不同，验证不会破坏原暂存区。
        code.write_text(
            "def add(a, b):\n    return 0\n",
            encoding="utf-8",
        )
        git(root, "add", "calculator.py")

        code.write_text(
            "def add(a, b):\n    return a + b\n",
            encoding="utf-8",
        )
        (root / "notes.txt").write_text(
            "新增文件",
            encoding="utf-8",
        )
        (root / ".env").write_text(
            "DEMO_KEY=test-only",
            encoding="utf-8",
        )

        head_before = git(root, "rev-parse", "HEAD")
        staged_before = git(root, "show", ":calculator.py")
        status_before = git(root, "status", "--porcelain")

        checkpoint = GitCheckpointStore(root).create()

        assert git(root, "rev-parse", "HEAD") == head_before
        assert git(root, "show", ":calculator.py") == staged_before
        assert git(root, "status", "--porcelain") == status_before
        print("创建检查点，不改变 HEAD、暂存区和工作区：通过")

        saved_code = git(
            root,
            "show",
            f"{checkpoint.commit_id}:calculator.py",
        )
        assert "return a + b" in saved_code
        assert git(
            root,
            "show",
            f"{checkpoint.commit_id}:notes.txt",
        ) == "新增文件"
        print("保存工作区修改和未忽略的新文件：通过")

        names = git(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            checkpoint.commit_id,
        ).splitlines()
        assert ".env" not in names

        assert git(root, "rev-parse", checkpoint.ref) == (
            checkpoint.commit_id
        )
        print("忽略 .env，专用引用可以查询检查点：通过")


if __name__ == "__main__":
    main()