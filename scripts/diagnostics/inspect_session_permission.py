from pathlib import Path

from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import PermissionSettings, ToolPermissionRequest


def main():
    engine = PermissionEngine(PermissionSettings(mode="default"))
    request = ToolPermissionRequest(
        tool_name="run_python", workspace=str(Path.cwd()),
        arguments={
            "args": ["-m", "unittest", "discover", "-v"],
            "timeout_seconds": 15,
        },
    )
    print("授权前：", engine.evaluate(request).action)
    engine.add_session_grant(request)
    print("授权后：", engine.evaluate(request).action)
    different = request.model_copy(deep=True, update={"arguments": {
        "args": ["-c", "print('hello')"], "timeout_seconds": 15,
    }})
    print("换一条命令：", engine.evaluate(different).action)


if __name__ == "__main__":
    main()
