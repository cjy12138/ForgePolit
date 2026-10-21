from pathlib import Path
from tempfile import TemporaryDirectory

from tests.skills.check_skill_selector import FakeSelectionModel

from forgepilot.skills.models import SkillDocument, SkillMetadata
from forgepilot.skills.selector import SkillSelector
from forgepilot.skills.service import SkillService
from forgepilot.skills.store import SkillStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.edit import build_edit_tools


def main():
    with TemporaryDirectory() as directory:
        store = SkillStore(
            Path(directory) / "skills",
            known_tools={
                "read_file",
                "list_files",
                "edit_file",
                "run_command",
            },
        )

        store.save(SkillDocument(
            metadata=SkillMetadata(
                name="read_project",
                description="阅读项目文件。",
                tools=["read_file"],
            ),
            content="先列出文件，再读取项目说明。",
        ))

        store.save(SkillDocument(
            metadata=SkillMetadata(
                name="shell_bugfix",
                description="在容器中运行命令修复问题。",
                tools=["run_command"],
            ),
            content="先运行已有测试，再定位问题。",
        ))

        # 当前工具池没有 run_command。
        pool = ToolRegistry(
            build_file_tools(),
            build_edit_tools(),
        )

        model = FakeSelectionModel("read_project")

        selector = SkillSelector(
            model,
            context_budget=ContextBudget(
                context_window=32000,
                max_output_tokens=512,
                safety_margin=2000,
            ),
        )

        service = SkillService(
            store=store,
            selector=selector,
            tool_pool=pool,
            base_tools={"list_files"},
        )

        # 1. 自动选择前，过滤缺少工具的候选。
        prepared = service.prepare(
            task="介绍项目文件。",
        )

        candidate_names = {
            item["name"]
            for item in model.request["candidates"]
        }

        assert candidate_names == {"read_project"}
        assert prepared.name == "read_project"

        enabled_names = {
            item["name"]
            for item in prepared.registry.definitions()
        }

        assert enabled_names == {"read_file", "list_files"}
        assert prepared.messages

        print("自动过滤候选，加载正文和对应工具：通过")

        # 2. 手动指定，不调用选择模型。
        calls_before = model.calls

        prepared = service.prepare(
            task="介绍项目文件。",
            requested_skill="read_project",
        )

        assert prepared.name == "read_project"
        assert model.calls == calls_before

        print("手动指定不请求选择模型：通过")

        # 3. 手动指定也不能绕过依赖检查。
        try:
            service.prepare(
                task="运行测试。",
                requested_skill="shell_bugfix",
            )
        except ValueError as exc:
            assert "run_command" in str(exc)
        else:
            raise AssertionError("缺少工具的 Skill 未被拒绝")

        assert model.calls == calls_before

        print("手动指定仍检查运行时工具依赖：通过")

        # 4. 模型选择不使用，保留原有工具池。
        model.selected_name = None

        prepared = service.prepare(
            task="没有适用 Skill 的普通任务。",
        )

        assert prepared.name is None
        assert prepared.messages == []
        assert prepared.registry is pool

        print("不使用 Skill 时保留原工具池：通过")


if __name__ == "__main__":
    main()