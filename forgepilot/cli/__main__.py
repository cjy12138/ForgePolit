"""轻量子命令分发；实际参数仍由对应模块解析。"""
from importlib import import_module
import sys

COMMANDS = {
    "coding": "forgepilot.cli.app",
    "bugfix": "forgepilot.cli.bugfix",
    "apply-fix": "forgepilot.cli.apply_fix",
    "export-fix": "forgepilot.cli.export_fix",
    "skills": "forgepilot.skills.__main__",
    "permissions": "forgepilot.permissions.__main__",
}


def main() -> int:
    if len(sys.argv) == 1 or sys.argv[1] in {"-h", "--help"}:
        print("ForgePilot — 命令行编程 Agent")
        print("用法：forgepilot <子命令> [参数]")
        print("  coding       交互编程、会话恢复及 coding/bugfix 路由")
        print("  bugfix       Docker 独立任务修复")
        print("  apply-fix    预览/确认应用修复补丁")
        print("  export-fix   导出已有任务结果")
        print("  skills       导入和查看 Markdown Skill")
        print("  permissions  查看、添加和删除权限规则")
        print("每个子命令支持 --help；也可使用 python -m forgepilot。")
        return 0
    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"未知子命令：{command}；使用 --help 查看用法。", file=sys.stderr)
        return 2
    arguments = sys.argv[:]
    try:
        sys.argv = [arguments[0] + " " + command, *arguments[2:]]
        return import_module(COMMANDS[command]).main() or 0
    finally:
        sys.argv = arguments


if __name__ == "__main__":
    raise SystemExit(main())
