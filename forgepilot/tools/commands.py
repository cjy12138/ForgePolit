import sys
from functools import partial
from pydantic import BaseModel, ConfigDict, Field
from forgepilot.environments.base import Environment
from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.permissions.command_checks import check_command_permissions

class RunPythonInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    args: list[str] = Field(
        min_length=1,
        description=(
            "传给 Python 的参数列表，例如 "
            "['-m', 'unittest', 'discover', '-v']。"
        ),
    )
    timeout_seconds: int = Field(
        default=15,
        ge=1,
        le=60,
        description="执行超时时间，单位为秒。",
    )

def run_python(
    *,
    args: list[str],
    context: ToolContext,
    environment: Environment,
    timeout_seconds: int = 15,
    python_executable: str = sys.executable,
) -> ToolResult:
    result = environment.execute(
        argv=[python_executable, "-X", "utf8", *args],
        cwd=context.workspace,
        timeout_seconds=timeout_seconds,
    )

    # 后面的输出整理和返回代码保持不变。

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

    if result.timed_out:
        output = (
                f"命令执行超过 {timeout_seconds} 秒，已超时。\n"
                + output
        )


    return ToolResult(
        ok=not result.timed_out,
        output=output,
        exit_code=result.returncode,
    )

def build_command_tools(
    *,
    environment: Environment,
    python_executable: str = sys.executable,
):
    return {
        "run_python": {
            "name": "run_python",
            "description": (
                "使用配置的执行环境运行 Python。"
                "可执行脚本或运行 unittest。"
                "根据 EXIT_CODE 判断执行结果，0 通常表示成功。"
            ),
            "input_model": RunPythonInput,
            "check_permissions": check_command_permissions,
            "handler": partial(
                run_python,
                environment=environment,
                python_executable=python_executable,
            ),
        }
    }
