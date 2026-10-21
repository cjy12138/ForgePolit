from collections.abc import Callable

from forgepilot.permissions.models import (
    PermissionRoute,
    PermissionSettings,
    ToolCheckResult,
    ToolPermissionRequest,
    PermissionRule,
    RuleLayer,
)
from forgepilot.permissions.modes import evaluate_permission_mode
from forgepilot.permissions.rules import RuleEngine
from forgepilot.permissions.tool_checks import ToolSafetyChecker
from forgepilot.permissions.operation import hash_permission_operation


class PermissionEngine:
    def __init__(self, settings: PermissionSettings):
        self.settings = settings.model_copy(deep=True)
        self.rules = RuleEngine(self.settings)
        self.safety = ToolSafetyChecker()

    def add_session_grant(self, request: ToolPermissionRequest) -> None:
        grant = PermissionRule(
            action="allow", tool=request.tool_name,
            operation_sha256=hash_permission_operation(request),
        )
        session_rules = []
        other_layers = []
        for layer in self.settings.layers:
            if layer.source == "session":
                session_rules.extend(layer.rules)
            else:
                other_layers.append(layer.model_copy(deep=True))
        if grant in session_rules:
            return
        session_rules.append(grant)
        settings = PermissionSettings(
            mode=self.settings.mode,
            layers=[*other_layers, RuleLayer(source="session", rules=session_rules)],
        )
        self.settings = settings
        self.rules = RuleEngine(settings)

    def replace_session_rules(self, rules: list[PermissionRule]) -> None:
        settings = PermissionSettings(
            mode=self.settings.mode,
            layers=[
                *[layer.model_copy(deep=True) for layer in self.settings.layers if layer.source != "session"],
                RuleLayer(source="session", rules=rules),
            ],
        )
        self.settings = settings
        self.rules = RuleEngine(settings)

    def evaluate(
        self,
        request: ToolPermissionRequest,
        *,
        check_permissions: Callable[
            [ToolPermissionRequest], ToolCheckResult
        ] | None = None,
    ) -> PermissionRoute:
        # 模式由权限配置决定，不接受工具参数指定模式。
        request = request.model_copy(
            deep=True,
            update={"mode": self.settings.mode},
        )

        rule = self.rules.evaluate(request)

        if rule.action == "deny":
            return PermissionRoute(
                action="deny",
                source="rule",
                reason=rule.reason,
            )

        try:
            mandatory = self.safety.check_mandatory(request)

            if mandatory.action == "deny":
                return PermissionRoute(
                    action="deny",
                    source="tool",
                    reason=mandatory.reason,
                )

            # 优先使用该工具注册的独立检查函数。
            checker = (
                check_permissions
                if check_permissions is not None
                else self.safety.check
            )

            checked = checker(request.model_copy(deep=True))

            if not isinstance(checked, ToolCheckResult):
                raise TypeError(
                    "工具权限检查必须返回 ToolCheckResult"
                )

        except Exception as exc:
            return PermissionRoute(
                action="deny",
                source="tool",
                reason=f"工具权限检查失败：{exc}",
            )

        # 即使规则 allow，工具的明确拒绝也不能被覆盖。
        if checked.action == "deny":
            return PermissionRoute(
                action="deny",
                source="tool",
                reason=checked.reason,
            )

        if rule.action == "allow":
            return PermissionRoute(
                action="allow",
                source="rule",
                reason=rule.reason,
            )

        if checked.action == "allow":
            return PermissionRoute(
                action="allow",
                source="tool",
                reason=checked.reason,
            )

        mode_result = evaluate_permission_mode(request)

        return PermissionRoute(
            action=mode_result.action,
            source="mode",
            reason=mode_result.reason,
        )
