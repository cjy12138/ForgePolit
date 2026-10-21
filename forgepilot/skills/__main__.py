import argparse
from pathlib import Path

from forgepilot.memory.scope import project_memory_root
from forgepilot.skills.catalog import KNOWN_TOOL_NAMES
from forgepilot.skills.models import render_skill
from forgepilot.skills.store import SkillStore


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]

    parser = argparse.ArgumentParser(
        description="管理项目 Skill 文档",
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        default=project_root / "demo_workspace",
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    add = commands.add_parser(
        "add",
        help="导入 SKILL.md",
    )
    add.add_argument("file", type=Path)
    add.add_argument(
        "--replace",
        action="store_true",
        help="显式更新同名 Skill",
    )

    commands.add_parser(
        "list",
        help="列出 Skill 元数据",
    )

    show = commands.add_parser(
        "show",
        help="查看完整 Skill",
    )
    show.add_argument("name")

    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()

    if not workspace.is_dir():
        parser.error("工作区不存在或不是目录")

    memory_root = project_memory_root(
        project_root=project_root,
        workspace=workspace,
    )

    scope = memory_root.relative_to(
        project_root / ".forgepilot" / "memory"
    )

    skills_root = (
        project_root
        / ".forgepilot"
        / "skills"
        / scope
    )

    try:
        store = SkillStore(
            skills_root,
            known_tools=KNOWN_TOOL_NAMES,
        )

        if args.command == "add":
            path = store.import_file(
                args.file.expanduser(),
                replace=args.replace,
            )
            print("Skill 已保存：", path)

        elif args.command == "list":
            items = store.list_metadata()

            if not items:
                print("当前项目还没有 Skill。")

            for metadata in items:
                print(
                    f"{metadata.name}: "
                    f"{metadata.description}"
                )
                print(
                    "  工具依赖：",
                    ", ".join(metadata.tools) or "无",
                )

        elif args.command == "show":
            document = store.read(args.name)
            print(render_skill(document))

        return 0

    except (ValueError, OSError) as exc:
        print("操作失败：", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())