from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.checkpoints.check_git_checkpoint import git
from forgepilot.checkpoints.cli import run_checkpoint_action
from forgepilot.sessions.inspection import inspect_branch
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

        git(root, "add", ".")
        git(root, "commit", "-m", "initial")

        store = SessionStore(
            root / ".forgepilot" / "session.jsonl"
        )
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=str(root),
        ))
        store.append_message({
            "role": "user",
            "content": "检查项目。",
        })
        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "本轮结束。",
        })

        run_checkpoint_action(
            "save", sessions=store, workspace=root
        )
        assert store.get_current_checkpoint() is not None

        code.write_text("VERSION = 2\n", encoding="utf-8")
        before = store.path.read_bytes()

        run_checkpoint_action(
            "preview", sessions=store, workspace=root
        )
        assert code.read_text(encoding="utf-8") == "VERSION = 2\n"
        assert store.path.read_bytes() == before

        with patch("builtins.input", return_value="cancel"):
            run_checkpoint_action(
                "restore", sessions=store, workspace=root
            )

        assert code.read_text(encoding="utf-8") == "VERSION = 2\n"
        assert store.path.read_bytes() == before
        print("预览与取消不改变代码或会话消息：通过")

        with patch("builtins.input", return_value="RESTORE"):
            run_checkpoint_action(
                "restore", sessions=store, workspace=root
            )

        assert code.read_text(encoding="utf-8") == "VERSION = 1\n"

        reopened = SessionStore(store.path)
        branch = reopened.get_current_branch()
        notice = branch[-1].payload

        assert notice["source"] == "checkpoint_restore"
        assert "历史中的文件内容和测试结果可能已经过期" in (
            notice["content"]
        )
        assert inspect_branch(branch) == "ready"
        print("确认后恢复，通知持久化且会话可继续：通过")


if __name__ == "__main__":
    main()