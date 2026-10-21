from forgepilot.permissions.models import (
    ModeCheckResult,
    ToolPermissionRequest,
)


def evaluate_permission_mode(
    request: ToolPermissionRequest,
) -> ModeCheckResult:
    """
    只处理前两层未决定的请求。
    调用前必须完成强制工具检查。
    """
    mode = request.mode

    if mode == "bypass":
        return ModeCheckResult(
            action="allow",
            reason="bypass 模式跳过后续询问。",
        )

    if mode == "plan":
        return ModeCheckResult(
            action="deny",
            reason="plan 模式拒绝尚未明确批准的操作。",
        )

    if mode == "auto":
        return ModeCheckResult(
            action="review",
            reason="auto 模式交给 AI 安全审查。",
        )

    if mode == "bubble":
        return ModeCheckResult(
            action="delegate",
            reason="交给父级权限入口处理。",
        )

    # default 和 accept_edits：
    # 没有在前两层获得批准的操作，需要用户确认。
    return ModeCheckResult(
        action="ask",
        reason="当前操作需要用户确认。",
    )
