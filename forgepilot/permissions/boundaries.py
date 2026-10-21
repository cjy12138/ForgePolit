import os
from pathlib import Path, PureWindowsPath

from forgepilot.permissions.models import (
    ToolCheckResult,
    ToolPermissionRequest,
)
from forgepilot.tools.extended_catalog import EXTENDED_PATH_FIELDS


PATH_TOOLS = frozenset({
    "read_file",
    "list_files",
    "grep_files",
    "edit_file",
    "write_file",
})

PROTECTED_PARTS = frozenset({
    ".git",
    ".forgepilot",
    ".claude",
    ".codex",
    ".agents",
})

WINDOWS_DEVICE_NAMES = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


def contains_protected_part(parts: tuple[str, ...]) -> bool:
    for part in parts:
        name = part.casefold()

        if name in PROTECTED_PARTS:
            return True

        # 不通过普通文件工具暴露或修改环境凭证文件。
        if name == ".env" or name.startswith(".env."):
            return True

    return False


def check_path_boundary(
    request: ToolPermissionRequest,
) -> ToolCheckResult:
    if request.tool_name in EXTENDED_PATH_FIELDS:
        for field in EXTENDED_PATH_FIELDS[request.tool_name]:
            value = request.arguments.get(field, ".")
            values = value if isinstance(value, list) else [value]
            for path in values:
                result = check_path_boundary(request.model_copy(update={
                    "tool_name": "read_file", "arguments": {"path": path},
                }))
                if result.action == "deny":
                    return result
        return ToolCheckResult(action="abstain", reason="所有扩展工具路径已通过边界检查。")

    if request.tool_name not in PATH_TOOLS:
        return ToolCheckResult(
            action="abstain",
            reason="当前工具不使用工作区直接路径字段。",
        )

    raw_path = request.arguments.get("path", ".")

    if not isinstance(raw_path, str) or not raw_path:
        return ToolCheckResult(
            action="deny",
            reason="文件路径必须是非空字符串。",
        )

    if "\x00" in raw_path:
        return ToolCheckResult(
            action="deny",
            reason="路径不能包含空字符。",
        )

    # 统一使用相对工作区的路径。
    relative = Path(raw_path.replace("\\", "/"))

    if relative.anchor or PureWindowsPath(raw_path).anchor:
        return ToolCheckResult(
            action="deny",
            reason="必须使用相对于工作区的路径。",
        )

    for part in relative.parts:
        if part in {".", ".."}:
            continue

        if ":" in part or part.endswith((" ", ".")):
            return ToolCheckResult(
                action="deny",
                reason="拒绝备用数据流或含歧义的路径名称。",
            )

        if (
            os.name == "nt"
            and part.split(".", 1)[0].casefold()
            in WINDOWS_DEVICE_NAMES
        ):
            return ToolCheckResult(
                action="deny",
                reason="不能访问 Windows 设备名称。",
            )

    if contains_protected_part(relative.parts):
        return ToolCheckResult(
            action="deny",
            reason="不能通过普通文件工具访问受保护路径。",
        )

    try:
        workspace = Path(request.workspace)

        if not workspace.is_absolute():
            raise ValueError("工作区必须是绝对路径")

        workspace = workspace.resolve(strict=True)

        if not workspace.is_dir():
            raise ValueError("工作区不是目录")

        target = (workspace / relative).resolve()

        if not target.is_relative_to(workspace):
            raise ValueError("路径不在允许的工作区内")

        resolved_parts = target.relative_to(workspace).parts

        if contains_protected_part(resolved_parts):
            raise ValueError("路径解析后指向受保护位置")

    except (ValueError, OSError, RuntimeError) as exc:
        return ToolCheckResult(
            action="deny",
            reason=f"路径边界检查失败：{exc}",
        )

    return ToolCheckResult(
        action="abstain",
        reason="路径边界检查通过，继续后续权限判断。",
    )
