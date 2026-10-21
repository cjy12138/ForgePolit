from pathlib import Path

from forgepilot.contracts import ToolContext
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import PermissionSettings
from forgepilot.permissions.service import PermissionService
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.sandbox_commands import RunCommandInput


def handler(*, command, timeout_seconds, context):
    print("实际处理器被调用：", command)
    return "模拟执行完成"


def main():
    tools = ToolRegistry({
        "run_command": {
            "name": "run_command",
            "description": "模拟命令工具，不执行真实命令。",
            "input_model": RunCommandInput,
            "handler": handler,
        },
    })

    tools.configure_permissions(
        service=PermissionService(
            PermissionEngine(
                PermissionSettings(mode="bypass")
            )
        )
    )

    context = ToolContext(
        workspace=Path.cwd(),
        task="输出 hello。",
    )

    for command in ["echo hello", "cat .env"]:
        print("\n请求：", command)

        result = tools.execute(
            "run_command",
            {"command": command},
            context,
        )

        print("结果：", result.ok, result.output)


if __name__ == "__main__":
    main()