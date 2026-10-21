from pathlib import Path

from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import (
    PermissionSettings,
    ToolPermissionRequest,
)


def main():
    examples = [
        ("default", "echo hello"),
        ("auto", "echo hello"),
        ("bypass", "echo hello"),
        ("bypass", "cat .env"),
    ]

    for mode, command in examples:
        engine = PermissionEngine(
            PermissionSettings(mode=mode)
        )

        request = ToolPermissionRequest(
            tool_name="run_command",
            arguments={"command": command},
            workspace=str(Path.cwd()),
        )

        result = engine.evaluate(request)

        print(
            f"{mode} | {command}"
            f" → {result.action}，来源：{result.source}"
        )


if __name__ == "__main__":
    main()