from forgepilot.permissions.boundaries import check_path_boundary
from forgepilot.permissions.command_checks import check_command_permissions
from forgepilot.permissions.models import (
    ToolCheckResult,
    ToolPermissionRequest,
)
from forgepilot.tools.extended_catalog import EXTENDED_READ_TOOLS


READ_ONLY_TOOLS = EXTENDED_READ_TOOLS | frozenset({
    "read_file",
    "list_files",
    "grep_files",
    "read_artifact",
    "read_memory",
    "glob_memory",
    "grep_memory",
    "list_memory_versions",
    "read_memory_version",
    "read_session_notes",
})

EDIT_TOOLS = frozenset({
    "edit_file",
    "write_file",
})

COMMAND_TOOLS = frozenset({
    "run_python",
    "run_command",
})


def is_plan_fork(request: ToolPermissionRequest) -> bool:
    if request.tool_name != "fork_tasks" or request.mode != "plan":
        return False
    from forgepilot.fork.models import ForkTasksInput
    try:
        params = ForkTasksInput.model_validate(request.arguments)
    except ValueError:
        return False
    return all(task.permission_mode in {None, "plan"} for task in params.tasks)


class ToolSafetyChecker:
    def check_mandatory(
        self,
        request: ToolPermissionRequest,
    ) -> ToolCheckResult:
        # 强制检查：规则 allow 和 bypass 都不能跳过。
        path_result = check_path_boundary(request)

        if path_result.action == "deny":
            return path_result

        # plan 不允许执行可能产生写入的操作。
        if (
            request.mode == "plan"
            and request.tool_name not in READ_ONLY_TOOLS
            and not is_plan_fork(request)
        ):
            return ToolCheckResult(
                action="deny",
                reason="plan 模式只允许已明确分类的只读工具。",
            )

        if request.tool_name in COMMAND_TOOLS:
            return check_command_permissions(request)

        return ToolCheckResult(
            action="abstain",
            reason="已通过当前实现的强制工具检查。",
        )

    def check(
        self,
        request: ToolPermissionRequest,
    ) -> ToolCheckResult:
        mandatory = self.check_mandatory(request)

        if mandatory.action == "deny":
            return mandatory

        if request.tool_name in READ_ONLY_TOOLS:
            return ToolCheckResult(
                action="allow",
                reason="已通过强制检查的只读工具。",
            )

        if request.tool_name in EDIT_TOOLS:
            if request.mode == "accept_edits":
                return ToolCheckResult(
                    action="allow",
                    reason=(
                        "accept_edits 模式允许"
                        "工作区内非受保护文件的编辑。"
                    ),
                )

            return ToolCheckResult(
                action="abstain",
                reason="编辑操作需要后续权限判断。",
            )

        if request.tool_name in COMMAND_TOOLS:
            return mandatory

        return ToolCheckResult(
            action="abstain",
            reason="工具没有明确的放行依据，继续后续权限判断。",
        )
