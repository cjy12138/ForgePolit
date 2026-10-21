from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.sessions.models import MessageEntry, SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.skills.service import PreparedSkill
from forgepilot.skills.task_state import (
    load_skill_state,
    save_skill_state,
)
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.edit import build_edit_tools


class CheckingModel:
    def __init__(self):
        self.before_request = None
        self.expected_tools = set()
        self.calls = 0

    def generate(self, *, messages, tools):
        if self.before_request is not None:
            self.before_request()

        names = {item["name"] for item in tools}
        assert names == self.expected_tools

        self.calls += 1

        return {
            "type": "final",
            "content": "本地模拟任务完成。",
        }


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        header = SessionHeader(
            project_id="test-project",
            owner_id="test-user",
            cwd=str(root),
        )

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(header)

        pool = ToolRegistry(
            build_file_tools(),
            build_edit_tools(),
        )

        states_dir = root / "skill-states"
        states_dir.mkdir()

        active = PreparedSkill(
            name="read_project",
            messages=[{
                "role": "system",
                "content": "只阅读文件。",
            }],
            registry=pool.select_tools({
                "read_file",
                "list_files",
            }),
        )

        saved_task_ids = []

        def save_current_skill():
            entry = sessions.get_current_branch()[-1]

            assert isinstance(entry, MessageEntry)
            assert entry.payload["role"] == "user"

            save_skill_state(
                states_dir / f"{entry.id}.json",
                session_id=header.session_id,
                prepared=active,
            )

            saved_task_ids.append(entry.id)

        def check_snapshot_exists():
            entry = sessions.get_current_branch()[-1]
            assert (states_dir / f"{entry.id}.json").is_file()

        model = CheckingModel()
        model.before_request = check_snapshot_exists

        agent = Agent(
            model=model,
            tools=active.registry,
            context=ToolContext(workspace=root),
            max_steps=1,
            session_store=sessions,
            task_started_hook=save_current_skill,
        )

        # 第一个任务使用只读配置。
        model.expected_tools = {"read_file", "list_files"}

        result = agent.run("阅读项目。")
        assert result["status"] == "completed"

        first_task_id = saved_task_ids[0]

        print("模型请求前已按用户节点保存快照：通过")

        # 第二个任务使用不同的配置。
        active = PreparedSkill(
            name="edit_project",
            messages=[{
                "role": "system",
                "content": "先阅读，再修改文件。",
            }],
            registry=pool.select_tools({
                "read_file",
                "edit_file",
            }),
        )

        agent.tools = active.registry
        agent.tool_definitions = active.registry.definitions()
        model.expected_tools = {"read_file", "edit_file"}

        result = agent.run("修改项目。")
        assert result["status"] == "completed"

        assert len(saved_task_ids) == 2
        assert saved_task_ids[0] != saved_task_ids[1]

        second_state = load_skill_state(
            states_dir / f"{saved_task_ids[1]}.json",
            session_id=header.session_id,
            tool_pool=pool,
        )

        assert second_state.name == "edit_project"

        print("不同任务分别保存 Skill 配置：通过")

        # 切回第一个用户任务节点，从那里继续形成新分支。
        sessions.move_cursor(first_task_id)

        task_entry = next(
            entry
            for entry in reversed(sessions.get_current_branch())
            if isinstance(entry, MessageEntry)
            and entry.payload.get("role") == "user"
        )

        restored = load_skill_state(
            states_dir / f"{task_entry.id}.json",
            session_id=header.session_id,
            tool_pool=pool,
        )

        assert restored.name == "read_project"
        assert restored.messages == [{
            "role": "system",
            "content": "只阅读文件。",
        }]

        agent.tools = restored.registry
        agent.tool_definitions = restored.registry.definitions()
        model.expected_tools = {"read_file", "list_files"}

        result = agent.resume()

        assert result["status"] == "completed"
        assert len(saved_task_ids) == 2
        assert model.calls == 3

        print("旧分支恢复旧配置，不重复保存任务快照：通过")


if __name__ == "__main__":
    main()