from forgepilot.permissions.approval import confirm_tool_permission
from pathlib import Path
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import (
    PermissionDecision,
    ToolCheckResult,
    ToolPermissionRequest,
    PermissionSettings,
)
from forgepilot.permissions.session_state import SessionPermissionState


class PermissionService:
    def __init__(
        self,
        engine: PermissionEngine,
        *,
        reviewer=None,
        approval=confirm_tool_permission,
        parent_authorize=None,
        session_state: SessionPermissionState | None = None,
    ):
        self.engine = engine
        self.reviewer = reviewer
        self.approval = approval
        self.parent_authorize = parent_authorize
        self.session_state = session_state

    def for_session(self, *, path: Path, session_id: str, workspace: Path):
        # 只继承项目策略和模式，不复制另一个会话的临时批准。
        settings = PermissionSettings(
            mode=self.engine.settings.mode,
            layers=[
                layer.model_copy(deep=True)
                for layer in self.engine.settings.layers
                if layer.source != "session"
            ],
        )
        state = SessionPermissionState(path, session_id=session_id, workspace=workspace)
        engine = PermissionEngine(settings)
        engine.replace_session_rules(state.load())
        return PermissionService(
            engine, reviewer=self.reviewer, approval=self.approval,
            parent_authorize=self.parent_authorize, session_state=state,
        )

    def authorize(
        self,
        request: ToolPermissionRequest,
        *,
        check_permissions=None,
    ) -> PermissionDecision:
        request = request.model_copy(
            deep=True,
            update={"mode": self.engine.settings.mode},
        )

        route = self.engine.evaluate(
            request,
            check_permissions=check_permissions,
        )

        if route.action in {"allow", "deny"}:
            return PermissionDecision(
                action=route.action,
                source=route.source,
                reason=route.reason,
            )

        if route.action == "review":
            reason = "尚未配置 AI 审查器，需要用户确认。"

            if self.reviewer is not None:
                try:
                    decision = self.reviewer.review(
                        request.model_copy(deep=True)
                    )

                    if not isinstance(decision, PermissionDecision):
                        raise TypeError("审查结果类型不合法")

                    if decision.action == "allow":
                        return decision

                    reason = decision.reason

                except Exception as exc:
                    reason = (
                        "AI 审查失败，需要用户确认。"
                        f"错误类型：{type(exc).__name__}"
                    )

            return self._ask(request, reason)

        if route.action == "delegate":
            if self.parent_authorize is None:
                return PermissionDecision(
                    action="deny",
                    source="parent",
                    reason="没有配置父级权限入口，不能批准执行。",
                )

            try:
                decision = self.parent_authorize(
                    request.model_copy(deep=True)
                )

                if (
                    not isinstance(decision, PermissionDecision)
                    or decision.action not in {"allow", "deny"}
                ):
                    raise ValueError("父级未返回最终批准或拒绝")

                return decision

            except Exception as exc:
                return PermissionDecision(
                    action="deny",
                    source="parent",
                    reason=(
                        "父级权限处理失败。"
                        f"错误类型：{type(exc).__name__}"
                    ),
                )

        return self._ask(request, route.reason)

    def _ask(
        self,
        request: ToolPermissionRequest,
        reason: str,
    ) -> PermissionDecision:
        try:
            decision = self.approval(
                request.model_copy(deep=True),
                ToolCheckResult(
                    action="abstain",
                    reason=reason,
                ),
            )

            if (
                not isinstance(decision, PermissionDecision)
                or decision.action not in {"allow", "deny"}
            ):
                raise ValueError("确认入口未返回最终批准或拒绝")

            if decision.action == "allow" and getattr(decision, "remember_session", False):
                try:
                    if self.session_state is not None:
                        rules = self.session_state.add(request)
                        self.engine.replace_session_rules(rules)
                    else:
                        self.engine.add_session_grant(request)
                except Exception as exc:
                    print(f"会话授权保存失败，本次批准仍有效：{type(exc).__name__}")
                    return PermissionDecision(
                        action="allow", source="parent",
                        reason="用户批准本次执行；会话授权保存失败，未复用该批准。",
                    )

            return decision

        except Exception as exc:
            return PermissionDecision(
                action="deny",
                source="parent",
                reason=(
                    "未能取得用户批准，工具不执行。"
                    f"错误类型：{type(exc).__name__}"
                ),
            )
