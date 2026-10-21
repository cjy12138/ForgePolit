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

        first_node = store.append_message({
            "role": "user",
            "content": "开始检查。",
        })

        service = SessionCheckpointService(
            store,
            GitCheckpointStore(root),
        )

        branch = store.get_current_branch()
        first = service.create()

        assert first.target_entry_id == first_node.id
        assert store.get_current_branch() == branch
        print("检查点关联当前节点，不移动游标：通过")

        second_node = store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "修改完成。",
        })
        code.write_text("VERSION = 2\n", encoding="utf-8")
        second = service.create()

        reopened = SessionStore(store.path)
        assert reopened.get_current_checkpoint() == second
        assert git(
            root, "show", f"{second.commit_id}:calculator.py"
        ) == "VERSION = 2"
        print("重新打开后找到对应的代码版本：通过")

        reopened.move_cursor(first_node.id)
        assert reopened.get_current_checkpoint() == first

        reopened.append_message({
            "role": "user",
            "content": "从这里开始另一条分支。",
        })
        assert reopened.get_current_checkpoint() == first
        print("新分支继承共同祖先的检查点，不混入另一分支记录：通过")

        # 切换对话分支尚不会自动恢复磁盘文件。
        assert code.read_text(encoding="utf-8") == "VERSION = 2\n"

        reopened.move_cursor(second_node.id)
        assert reopened.get_current_checkpoint() == second
        print("切回原分支，仍可查询原来的检查点：通过")

        branch_before = reopened.get_current_branch()

        # 当前位置是第二个节点，但可以查询第一个节点。
        assert (
            reopened.get_checkpoint_at(first_node.id)
            == first
        )

        assert (
            reopened.get_checkpoint_at(second_node.id)
            == second
        )

        # 查询不会移动当前位置。
        assert reopened.get_current_branch() == branch_before
        assert reopened.get_current_checkpoint() == second

        print("查询目标位置的检查点，不移动当前游标：通过")


if __name__ == "__main__":
    main()