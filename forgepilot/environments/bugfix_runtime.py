from dataclasses import dataclass
from pathlib import Path

from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.environments.bugfix_workspace import (
    BugfixWorkspace,
    BugfixWorkspaceManager,
)
from forgepilot.environments.docker import DockerEnvironment
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.commands import build_command_tools
from forgepilot.tools.edit import build_edit_tools
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.extended import build_extended_tools
from forgepilot.tools.search import build_search_tools
from forgepilot.tools.sandbox_commands import (
    build_sandbox_command_tools,
)


class BugfixToolRegistry(ToolRegistry):
    def __init__(self, environment, *tool_groups):
        self.environment = environment
        super().__init__(*tool_groups)

    def with_tools(self, *tool_groups):
        # 扩展工具时保留容器停止后的执行保护。
        return self._copy_permission_config(BugfixToolRegistry(
            self.environment,
            self._tools,
            *tool_groups,
        ))

    def _new_registry(self, entries) -> "BugfixToolRegistry":
        return BugfixToolRegistry(
            self.environment,
            entries,
        )

    def _execute(self, name, arguments, context, *, call_id, execution):
        if self.environment.container_id is None:
            self._audit_event(
                call_id, "decision", action="deny", source="environment",
                reason_code="container_stopped",
            )
            return ToolResult(
                ok=False,
                output="任务容器已经停止，本次工具未执行。",
            )

        return super()._execute(
            name, arguments, context, call_id=call_id, execution=execution,
        )

@dataclass
class BugfixRuntime:
    task: BugfixWorkspace
    environment: DockerEnvironment
    context: ToolContext
    tools: BugfixToolRegistry

    def close(self) -> None:
        # 只关闭容器，保留任务副本及修复成果。
        self.environment.close()



def start_bugfix_runtime(
    *,
    source: Path,
    tasks_root: Path,
    image: str = "python:3.13-slim",
    existing_task: BugfixWorkspace | None = None,
) -> BugfixRuntime:
    manager = BugfixWorkspaceManager(
        source=source,
        tasks_root=tasks_root,
    )

    task = existing_task if existing_task is not None else manager.create()
    if task.source_workspace != manager.source or task.path != manager.tasks_root / task.id:
        raise ValueError("任务副本不属于当前管理器")

    environment = DockerEnvironment(
        workspace=task.path,
        image=image,
    )
    # 固定任务容器名称：进程被强制结束后，恢复时可清理遗留容器。
    environment.container_name = f"forgepilot-bugfix-{task.id}"

    try:
        if existing_task is not None:
            environment.container_id = environment.container_name
            environment.close()
        environment.start()

        context = ToolContext(workspace=task.path)

        tools = BugfixToolRegistry(
            environment,
            build_file_tools(),
            build_extended_tools(),
            build_search_tools(),
            build_edit_tools(),
            build_command_tools(
                environment=environment,
                python_executable="python",
            ),
            build_sandbox_command_tools(
                environment=environment,
            ),
        )

        return BugfixRuntime(
            task=task,
            environment=environment,
            context=context,
            tools=tools,
        )

    except Exception as exc:
        environment.close()
        raise RuntimeError(
            f"bugfix 环境启动失败，任务副本保留在：{task.path}"
        ) from exc


