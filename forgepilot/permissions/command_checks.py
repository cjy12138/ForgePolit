from pathlib import PurePosixPath

from forgepilot.permissions.bash_analysis import BashCommandAnalyzer
from forgepilot.permissions.bash_operands import extract_path_operands
from forgepilot.permissions.boundaries import contains_protected_part
from forgepilot.permissions.models import ToolCheckResult, ToolPermissionRequest


def check_literal_path(target: str) -> str | None:
    """POSIX 字面量路径规则；不声称解析了容器内符号链接或 cwd。"""
    if not target or "\x00" in target:
        return "文件目标为空或不合法。"
    if target == "/dev/null":
        return None
    path = PurePosixPath(target)
    if contains_protected_part(path.parts):
        return "命令访问了受保护路径。"
    if path.is_absolute() or ".." in path.parts:
        return "命令文件目标仅支持不含 .. 的相对工作区路径。"
    return None


def check_command_permissions(
    request: ToolPermissionRequest,
) -> ToolCheckResult:
    """拒绝明确违规的文件操作数和重定向，其余交给后续判断。"""
    if request.tool_name not in {"run_command", "run_python"}:
        return ToolCheckResult(action="abstain", reason="不是命令工具。")

    if request.mode == "plan":
        return ToolCheckResult(action="deny", reason="plan 模式不允许执行命令。")

    if request.tool_name == "run_python":
        return ToolCheckResult(
            action="abstain",
            reason="Python 参数不是 Bash 命令，需要后续权限判断。",
        )

    command = request.arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolCheckResult(action="deny", reason="命令必须是非空字符串。")

    analysis = BashCommandAnalyzer().analyze(command)
    if not analysis.parsed:
        return ToolCheckResult(
            action="abstain",
            reason="Bash 结构解析失败，不能据此放行，需要后续审查。",
        )

    unresolved = False
    for operation in analysis.operations:
        paths, uncertain = extract_path_operands(operation)
        unresolved = unresolved or uncertain
        for path in paths:
            reason = check_literal_path(path)
            if reason is not None:
                return ToolCheckResult(action="deny", reason=reason)

    for redirect in analysis.redirects:
        if redirect.kind == "unknown":
            unresolved = True
            continue
        if redirect.kind != "file":
            continue

        target = redirect.static_target
        if target is None:
            unresolved = True
            continue

        reason = check_literal_path(target)
        if reason is not None:
            return ToolCheckResult(action="deny", reason=reason)

    if unresolved:
        return ToolCheckResult(
            action="abstain",
            reason="存在动态目标、未知命令或未解释的选项，需要后续审查。",
        )

    return ToolCheckResult(
        action="abstain",
        reason="未发现明确违规的文件操作，命令整体仍需后续权限判断。",
    )
