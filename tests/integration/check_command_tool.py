from forgepilot.paths import PROJECT_ROOT
from pathlib import Path

from forgepilot.contracts import ToolContext
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.commands import build_command_tools
from forgepilot.environments.factory import get_environment

def main():
    project_root = PROJECT_ROOT
    context = ToolContext(
        workspace=project_root / "demo_workspace",
    )
    environment = get_environment({
        "environment_class": "local",
    })
    registry = ToolRegistry(build_command_tools(environment=environment))

    result = registry.execute(
        "run_python",
        {
            "args": ["-m", "unittest", "discover", "-v"],
        },
        context=context,
    )

    print("工具是否成功返回：", result.ok)
    print(result.output)

    print("结构化退出码：", result.exit_code)

    assert result.ok, result.output
    assert result.exit_code == 0, result.output

    failed = registry.execute(
        "run_python",
        {"args": ["-c", "import sys; sys.exit(3)"]},
        context=context,
    )

    assert failed.ok, failed.output
    assert failed.exit_code == 3
    print("非零退出码传递：通过")
    timed_out = registry.execute(
        "run_python",
        {
            "args": [
                "-c",
                (
                    "import sys, time; "
                    "print('started', flush=True); "
                    "print('diagnostic', file=sys.stderr, flush=True); "
                    "time.sleep(10)"
                ),
            ],
            "timeout_seconds": 2,
        },
        context=context,
    )

    assert not timed_out.ok
    assert timed_out.exit_code is None
    assert "STATUS: timeout" in timed_out.output
    assert "started" in timed_out.output
    assert "diagnostic" in timed_out.output

    print("超时保留标准输出与错误输出：通过")


if __name__ == "__main__":
    main()