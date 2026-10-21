from pathlib import Path
from tempfile import TemporaryDirectory

from tests.skills.check_skill_selector import FakeSelectionModel

from forgepilot.skills.models import SkillDocument, SkillMetadata
from forgepilot.skills.selector import SkillSelector
from forgepilot.skills.service import SkillService
from forgepilot.skills.store import SkillStore
from forgepilot.skills.task_state import (
    load_skill_state,
    save_skill_state,
)
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.edit import build_edit_tools


def tool_names(registry):
    return {
        item["name"]
        for item in registry.definitions()
    }


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        store = SkillStore(
            root / "skills",
            known_tools={
                "read_file",
                "list_files",
                "edit_file",
            },
        )

        metadata = SkillMetadata(
            name="read_project",
            description="阅读项目文件。",
            tools=["read_file"],
        )

        store.save(SkillDocument(
            metadata=metadata,
            content="先列出文件，再读取项目说明。",
        ))

        pool = ToolRegistry(
            build_file_tools(),
            build_edit_tools(),
        )

        model = FakeSelectionModel("read_project")

        service = SkillService(
            store=store,
            selector=SkillSelector(
                model,
                context_budget=ContextBudget(
                    context_window=32000,
                    max_output_tokens=512,
                    safety_margin=2000,
                ),
            ),
            tool_pool=pool,
            base_tools={"list_files"},
        )

        prepared = service.prepare(
            task="介绍项目文件。",
            requested_skill="read_project",
        )

        state_path = root / "skill-state.json"
        session_id = "a" * 32

        save_skill_state(
            state_path,
            session_id=session_id,
            prepared=prepared,
        )

        restored = load_skill_state(
            state_path,
            session_id=session_id,
            tool_pool=pool,
        )

        assert restored.name == prepared.name
        assert restored.messages == prepared.messages
        assert tool_names(restored.registry) == {
            "read_file",
            "list_files",
        }

        print("保存并恢复 Skill 正文和工具集合：通过")

        # 修改库里的 Skill，已有任务仍使用保存的版本。
        store.save(
            SkillDocument(
                metadata=metadata,
                content="新版指南：先读取 README.md。",
            ),
            replace=True,
        )

        restored = load_skill_state(
            state_path,
            session_id=session_id,
            tool_pool=pool,
        )

        assert restored.messages == prepared.messages

        new_task = service.prepare(
            task="介绍项目文件。",
            requested_skill="read_project",
        )

        assert new_task.messages != restored.messages
        assert model.calls == 0

        print("已有任务保留旧版，新任务加载新版：通过")

        # 不能误用其他会话的状态。
        try:
            load_skill_state(
                state_path,
                session_id="b" * 32,
                tool_pool=pool,
            )
        except ValueError as exc:
            assert "不属于当前会话" in str(exc)
        else:
            raise AssertionError("错误会话的状态未被拒绝")

        print("拒绝其他会话的 Skill 状态：通过")

        # 恢复配置不能凭空增加当前环境没有的工具。
        reduced_pool = pool.select_tools({"list_files"})

        try:
            load_skill_state(
                state_path,
                session_id=session_id,
                tool_pool=reduced_pool,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("缺少必要工具时仍然恢复成功")

        print("恢复时仍检查工具是否可用：通过")


if __name__ == "__main__":
    main()