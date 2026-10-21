from pathlib import Path

from forgepilot.permissions.models import ToolPermissionRequest
from forgepilot.permissions.modes import evaluate_permission_mode


def main():
    modes = [
        "default",
        "accept_edits",
        "plan",
        "bypass",
        "auto",
        "bubble",
    ]

    for mode in modes:
        request = ToolPermissionRequest(
            tool_name="run_command",
            arguments={
                "command": "python -m unittest discover -v",
            },
            workspace=str(Path.cwd()),
            mode=mode,
        )

        result = evaluate_permission_mode(request)
        print(f"{mode}: {result.action} — {result.reason}")


if __name__ == "__main__":
    main()
