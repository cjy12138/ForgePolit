import sys
from pathlib import Path

from forgepilot.contracts import ToolContext
from forgepilot.environments.base import ExecutionResult
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.commands import build_command_tools


class FakeEnvironment:
    def __init__(self):
        self.calls = []

    def execute(
            self,
            *,
            argv: list[str],
            cwd: Path,
            timeout_seconds: int,
    ) -> ExecutionResult:
        self.calls.append({
            "argv": argv,
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
        })

        return ExecutionResult(
            returncode=7,
            stdout="模拟输出",
            stderr="模拟失败",
        )


def main():
    environment = FakeEnvironment()
    context = ToolContext(workspace=Path.cwd())

    registry = ToolRegistry(
        build_command_tools(environment=environment)
    )
    # 仅调用 FakeEnvironment，权限行为由独立检查脚本覆盖。
    registry.configure_permissions(mode="bypass")

    result = registry.execute(
        "run_python",
        {
            "args": ["-m", "unittest"],
            "timeout_seconds": 5,
        },
        context=context,
    )

    assert len(environment.calls) == 1
    call = environment.calls[0]

    assert call["argv"] == [
        sys.executable, "-X", "utf8", "-m", "unittest",
    ]
    assert call["cwd"] == context.workspace
    assert call["timeout_seconds"] == 5

    assert result.ok
    assert result.exit_code == 7
    assert "模拟输出" in result.output

    print("外部环境收到参数，结果正确传回：通过")


if __name__ == "__main__":
    main()
