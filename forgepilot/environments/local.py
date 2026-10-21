import subprocess
from pathlib import Path
from forgepilot.environments.base import ExecutionResult
import os
from forgepilot.environments.variables import command_environment

def decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value

def stop_process_tree(process: subprocess.Popen) -> str:
    if process.poll() is not None:
        return "父进程已经退出，无法据此确认后代进程已清理。"

    warning = ""

    if os.name == "nt":
        try:
            result = subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
                timeout=5,
                shell=False,
            )

            if result.returncode != 0:
                raw_detail = result.stderr or result.stdout

                # taskkill 在 Windows 上通常使用系统编码输出。
                detail = raw_detail.decode(
                    "mbcs",
                    errors="replace",
                ).strip()

                warning = (
                    f"进程树清理命令返回 {result.returncode}；"
                    f"原因：{detail or '未提供错误信息'}；"
                    "后代进程清理状态未确认。"
                )

        except (OSError, subprocess.TimeoutExpired) as exc:
            warning = (
                f"进程树清理失败：{type(exc).__name__}；"
                "后代进程清理状态未确认。"
            )
    else:
        warning = "当前尚未实现此平台的进程树清理。"

    # 无论进程树清理是否成功，都尝试终止直接子进程。
    if process.poll() is None:
        process.kill()

    return warning


class LocalEnvironment:
    def execute(
        self,
        *,
        argv: list[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        if not argv:
            raise ValueError("执行参数不能为空")

        if timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")

        cwd = cwd.resolve()

        if not cwd.is_dir():
            raise ValueError("工作目录不存在或不是目录")

        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=command_environment(),
        )

        try:
            try:
                stdout, stderr = process.communicate(
                    timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired:
                cleanup_warning = stop_process_tree(process)

                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    # 后代进程可能仍持有输出管道。
                    # 不再无限等待，但此时不能宣称进程树已清理。
                    stdout = decode_output(exc.stdout)
                    stderr = (
                        decode_output(exc.stderr)
                        + "\n输出管道未及时关闭，"
                        "后代进程清理状态尚未确认。"
                    )
                if cleanup_warning:
                    stderr += "\nCLEANUP_WARNING: " + cleanup_warning
                return ExecutionResult(
                    returncode=None,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=True,
                )

            return ExecutionResult(
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )

        finally:
            # 异常或用户中断时，也尝试回收直接子进程。
            try:
                if process.poll() is None:
                    process.kill()

                process.wait(timeout=5)
            finally:
                if process.stdout is not None:
                    process.stdout.close()

                if process.stderr is not None:
                    process.stderr.close()
