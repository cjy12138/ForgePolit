from pathlib import Path
from tempfile import TemporaryDirectory

from tests.checkpoints.check_git_checkpoint import git
from forgepilot.checkpoints.git import GitCheckpointStore


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
        code.write_text("VERSION = 1\n", encoding="utf-8")
        (root / "old.txt").write_text("旧文件", encoding="utf-8")

        git(root, "add", ".")
        git(root, "commit", "-m", "initial")

        checkpoints = GitCheckpointStore(root)
        target = checkpoints.create()

        # 原暂存区故意保留一个不同版本。
        code.write_text("VERSION = 99\n", encoding="utf-8")
        git(root, "add", "calculator.py")

        code.write_text("VERSION = 2\n", encoding="utf-8")
        (root / "old.txt").unlink()
        (root / "new.txt").write_text("新文件", encoding="utf-8")
        (root / ".env").write_text("TEST=only", encoding="utf-8")

        plan = checkpoints.prepare_restore(target.id)

        head_before = git(root, "rev-parse", "HEAD")
        index_before = git(root, "show", ":calculator.py")

        checkpoints.restore(plan)

        assert code.read_text(encoding="utf-8") == "VERSION = 1\n"
        assert (root / "old.txt").read_text(encoding="utf-8") == "旧文件"
        assert not (root / "new.txt").exists()
        print("恢复修改、新增和删除的文件状态：通过")

        assert git(root, "rev-parse", "HEAD") == head_before
        assert git(root, "show", ":calculator.py") == index_before
        assert (root / ".env").read_text(encoding="utf-8") == "TEST=only"
        print("HEAD、原暂存区和忽略文件保持不变：通过")

        # 恢复前备份也能作为目标。
        undo_plan = checkpoints.prepare_restore(plan.backup.id)
        checkpoints.restore(undo_plan)

        assert code.read_text(encoding="utf-8") == "VERSION = 2\n"
        assert (root / "new.txt").exists()
        assert not (root / "old.txt").exists()
        print("通过备份检查点恢复之前的代码：通过")

        stale_plan = checkpoints.prepare_restore(target.id)
        code.write_text("VERSION = 3\n", encoding="utf-8")

        try:
            checkpoints.restore(stale_plan)
        except ValueError as exc:
            assert "预览后代码已变化" in str(exc)
        else:
            raise AssertionError("过期恢复计划没有被拒绝")

        assert code.read_text(encoding="utf-8") == "VERSION = 3\n"
        assert (root / "new.txt").exists()
        print("预览后代码变化时，拒绝覆盖：通过")


if __name__ == "__main__":
    main()