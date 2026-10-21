from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from forgepilot.contracts import ToolContext
from forgepilot.environments.bugfix_runtime import BugfixToolRegistry
from forgepilot.skills.loader import SkillLoader
from forgepilot.skills.models import SkillDocument, SkillMetadata
from forgepilot.skills.store import SkillStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.edit import build_edit_tools


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()

        source = workspace / "sample.txt"
        source.write_text("original", encoding="utf-8")

        context = ToolContext(workspace=workspace)

        store = SkillStore(
            root / "skills",
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
            content="先列出文件，再读取相关内容。",
        ))

        pool = ToolRegistry(
            build_file_tools(),
            build_edit_tools(),
        )

        loader = SkillLoader(
            store=store,
            tool_pool=pool,
            base_tools={"list_files"},
        )

        loaded = loader.load("read_project")

        enabled = {
            item["name"]
            for item in loaded.registry.definitions()
        }

        assert enabled == {"read_file", "list_files"}
        assert "先列出文件" in loaded.messages[0]["content"]

        print("基础工具与 Skill 依赖正确合并：通过")

        # 原工具池仍保留 edit_file。
        assert "edit_file" in {
            item["name"]
            for item in pool.definitions()
        }

        # 但本次没有启用，不能执行修改。
        rejected = loaded.registry.execute(
            "edit_file",
            {
                "path": "sample.txt",
                "old_text": "original",
                "new_text": "changed",
            },
            context,
        )

        assert not rejected.ok
        assert source.read_text(encoding="utf-8") == "original"

        print("原工具池不变，未启用工具不能产生修改：通过")

        result = loaded.registry.execute(
            "read_file",
            {"path": "sample.txt"},
            context,
        )

        assert result.ok
        assert "original" in result.output

        print("启用的工具可以正常执行：通过")

        # 模拟已停止的容器，不启动真实 Docker。
        environment = SimpleNamespace(container_id=None)

        bugfix_pool = BugfixToolRegistry(
            environment,
            build_file_tools(),
        )

        selected = bugfix_pool.select_tools({"read_file"})

        assert isinstance(selected, BugfixToolRegistry)
        assert selected.environment is environment

        result = selected.execute(
            "read_file",
            {"path": "sample.txt"},
            context,
        )

        assert not result.ok
        assert "任务容器已经停止" in result.output

        print("筛选后保留 bugfix 容器停止保护：通过")

        # 名称合法，但当前工具池不提供 run_command。
        store.save(SkillDocument(
            metadata=SkillMetadata(
                name="shell_task",
                description="需要容器命令工具的任务。",
                tools=["run_command"],
            ),
            content="运行项目已有测试。",
        ))

        try:
            loader.load("shell_task")
        except ValueError as exc:
            assert "run_command" in str(exc)
        else:
            raise AssertionError("缺失工具依赖未被拒绝")

        print("拒绝当前运行时无法提供的工具依赖：通过")


if __name__ == "__main__":
    main()