from functools import partial

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.environments.docker import DockerEnvironment
from forgepilot.permissions.command_checks import check_command_permissions


class RunCommandInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    command: str = Field(
        min_length=1,
        description="在任务容器中执行的 Bash 命令。",
    )

    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=120,
        description="命令超时时间，单位为秒。",
    )


def run_command(
    *,
    command: str,
    context: ToolContext,
    environment: DockerEnvironment,
    timeout_seconds: int = 30,
) -> ToolResult:
    result = environment.execute(
        argv=["bash", "-lc", command],
        cwd=context.workspace,
        timeout_seconds=timeout_seconds,
    )

    status = "timeout" if result.timed_out else "exited"
    exit_code = (
        str(result.returncode)
        if result.returncode is not None
        else "unknown"
    )

    output = (
        f"STATUS: {status}\n"
        f"EXIT_CODE: {exit_code}\n"
        f"STDOUT:\n{result.stdout or '(empty)'}\n"
        f"STDERR:\n{result.stderr or '(empty)'}"
    )

    return ToolResult(
        ok=not result.timed_out,
        output=output,
        exit_code=result.returncode,
    )


def build_sandbox_command_tools(
    *,
    environment: DockerEnvironment,
):
    if not isinstance(environment, DockerEnvironment):
        raise TypeError("通用命令工具只能绑定 Docker 执行环境")

    return {
        "run_command": {
            "name": "run_command",
            "description": (
                "在 bugfix 任务容器中执行 Bash 命令，"
                "可读取、搜索、修改文件或运行已有测试。"
                "每次使用独立 shell，默认目录为任务工作区；"
                "上次的 cd 和临时变量不会延续，文件修改会保留。"
                "根据 EXIT_CODE 判断命令结果。"
                "超时会关闭任务容器。"
            ),
            "input_model": RunCommandInput,
            "check_permissions": check_command_permissions,
            "handler": partial(
                run_command,
                environment=environment,
            ),
        }
    }
