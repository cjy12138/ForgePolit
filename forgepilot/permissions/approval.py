import json

from forgepilot.permissions.models import (
    PermissionDecision,
    ApprovalDecision,
    ToolCheckResult,
    ToolPermissionRequest,
)


def confirm_tool_permission(
    request: ToolPermissionRequest,
    checked: ToolCheckResult,
) -> PermissionDecision:
    """用户选择本次批准，或当前会话内批准完全相同的操作。"""
    print(f"\n工具权限确认：{request.tool_name}")
    print("工作区：", request.workspace)
    print("参数：", json.dumps(request.arguments, ensure_ascii=False))
    print("原因：", checked.reason)
    try:
        answer = input("允许执行？y=仅本次，s=当前会话内相同操作，其余输入拒绝：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return ApprovalDecision(
        action="allow" if answer in {"y", "s"} else "deny",
        source="parent",
        reason=(
            "用户批准当前会话内相同操作。" if answer == "s"
            else "用户批准本次执行。" if answer == "y"
            else "未获得用户批准。"
        ),
        remember_session=answer == "s",
    )
