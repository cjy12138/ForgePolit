import shutil
import subprocess
from pathlib import Path
from uuid import uuid4
from forgepilot.environments.base import ExecutionResult
from forgepilot.environments.local import decode_output

class DockerEnvironment:
    def __init__(
        self,
        *,
        workspace: Path,
        image: str = "python:3.13-slim",
    ):
        self.workspace = workspace.resolve()
        self.image = image
        self.container_name = f"forgepilot-{uuid4().hex}"
        self.container_id: str | None = None

        if not self.workspace.is_dir():
            raise ValueError("工作区不存在或不是目录")

        # Docker --mount 使用逗号分隔配置项。
        if "," in str(self.workspace):
            raise ValueError("当前版本不支持路径中包含逗号")

    def start(self) -> None:
        if self.container_id is not None:
            raise RuntimeError("容器已经启动")

        if shutil.which("docker") is None:
            raise RuntimeError("未找到 docker，请安装并启动 Docker Desktop")

        command = [
            "docker",
            "run",
            "-d",
            "--init",
            "--name",
            self.container_name,
            "--network",
            "none",
            "--mount",
            f"type=bind,source={self.workspace},target=/workspace",
            "--workdir",
            "/workspace",
            self.image,
            "sleep",
            "infinity",
        ]

        # 先记录名称：启动超时时，也能据此尝试清理。
        self.container_id = self.container_name

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=True,
                shell=False,
            )

            container_id = result.stdout.strip()

            if not container_id:
                raise RuntimeError("Docker 未返回容器 ID")

            self.container_id = container_id

        except Exception:
            self.close()
            raise

    def execute(
            self,
            *,
            argv: list[str],
            cwd: Path,
            timeout_seconds: int,
    ) -> ExecutionResult:
        if self.container_id is None:
            raise RuntimeError("容器尚未启动或已经关闭")

        if not argv:
            raise ValueError("执行参数不能为空")

        if timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")

        cwd = cwd.resolve()

        if not cwd.is_dir():
            raise ValueError("工作目录不存在")

        if not cwd.is_relative_to(self.workspace):
            raise ValueError("工作目录不在任务工作区内")

        relative = cwd.relative_to(self.workspace)

        container_cwd = (
            "/workspace"
            if relative == Path(".")
            else f"/workspace/{relative.as_posix()}"
        )

        command = [
            "docker",
            "exec",
            "--workdir",
            container_cwd,
            self.container_id,
            *argv,
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
            )

        except subprocess.TimeoutExpired as exc:
            # 不能只停止宿主机上的 docker exec 客户端。
            # 第一版直接销毁任务容器，停止其中的执行。
            self.close()

            return ExecutionResult(
                returncode=None,
                stdout=decode_output(exc.stdout),
                stderr=(
                        decode_output(exc.stderr)
                        + "\n执行超时，任务容器已删除；"
                          "任务文件保留，不能继续使用这个容器。"
                ),
                timed_out=True,
            )

        except (KeyboardInterrupt, OSError):
            self.close()
            raise

        return ExecutionResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def close(self) -> None:
        if self.container_id is None:
            return

        result = subprocess.run(
            ["docker", "rm", "-f", self.container_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )

        if (
            result.returncode != 0
            and "No such container" not in result.stderr
        ):
            raise RuntimeError(
                f"容器清理失败：{result.stderr.strip()}"
            )

        self.container_id = None