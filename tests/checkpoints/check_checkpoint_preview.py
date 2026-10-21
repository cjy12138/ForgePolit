from pathlib import Path
from tempfile import TemporaryDirectory

from tests.checkpoints.check_git_checkpoint import git
from forgepilot.checkpoints.git import GitCheckpointStore
from forgepilot.checkpoints.service import SessionCheckpointService
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        git(root, "init")
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@local.invalid")

        (root / ".gitignore").write_text(
            ".forgepilot/\n.env\n",
            encoding="utf-8",
        )
        code = root / "calculator.py"
        code.write_text("VERSION = 1\n", encoding="utf-8")
        (root / "old.txt").write_text("旧文件", encoding="utf-8")

        git(root, "add", ".")
        git(root, "commit", "-m", "initial")

        sessions = SessionStore(
            root / ".forgepilot" / "session.jsonl"
        )
        sessions.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=str(root),
        ))
        sessions.append_message({
            "role": "user",
            "content": "开始任务。",
        })

        service = SessionCheckpointService(
            sessions,
            GitCheckpointStore(root),
        )
        target = service.create()

        code.write_text("VERSION = 2\n", encoding="utf-8")
        (root / "old.txt").unlink()
        (root / "new.txt").write_text("新文件", encoding="utf-8")

        head_before = git(root, "rev-parse", "HEAD")
        status_before = git(root, "status", "--porcelain")
        session_before = sessions.path.read_bytes()

        plan = service.prepare_current_restore()

        assert plan.target.commit_id == target.commit_id
        assert "M\tcalculator.py" in plan.changes
        assert "A\told.txt" in plan.changes
        assert "D\tnew.txt" in plan.changes
        assert "-VERSION = 2" in plan.patch
        assert "+VERSION = 1" in plan.patch
        print("预览方向为当前代码到目标检查点：通过")

        assert git(
            root,
            "show",
            f"{plan.backup.commit_id}:calculator.py",
        ) == "VERSION = 2"
        assert git(
            root,
            "show",
            f"{plan.backup.commit_id}:new.txt",
        ) == "新文件"
        print("恢复前的当前代码已保存为备份：通过")

        assert code.read_text(encoding="utf-8") == "VERSION = 2\n"
        assert not (root / "old.txt").exists()
        assert (root / "new.txt").exists()
        assert git(root, "rev-parse", "HEAD") == head_before
        assert git(root, "status", "--porcelain") == status_before
        assert sessions.path.read_bytes() == session_before
        print("预览不恢复文件、不移动 HEAD、不修改会话：通过")


if __name__ == "__main__":
    main()