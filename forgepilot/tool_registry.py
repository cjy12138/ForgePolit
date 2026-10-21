
from copy import deepcopy
from time import monotonic
from uuid import uuid4

from forgepilot.contracts import ToolResult
from forgepilot.permissions.approval import confirm_tool_permission
from forgepilot.permissions.models import (
    PermissionDecision,
    PermissionSettings,
    ToolCheckResult,
    ToolPermissionRequest,
)
from pydantic import BaseModel, ValidationError
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.service import PermissionService
from forgepilot.permissions.audit import PermissionAuditLog, redact_arguments, text_fingerprint
from forgepilot.tools.guards import RepeatedCallGuard
class ToolRegistry:
    def __init__(self, *tool_groups):
        self._tools = {}
        self._repeat_guard = RepeatedCallGuard()
        self.audit_log = None
        self._audit_warning_shown = False
        self.configure_permissions(
            resolver=confirm_tool_permission,
        )


        for group in tool_groups:
            for key, entry in group.items():
                name = entry.get("name")

                if not isinstance(name, str) or not name.strip():
                    raise ValueError("工具名称必须是非空字符串")

                if key != name:
                    raise ValueError(
                        f"注册键 {key!r} 与工具名称 {name!r} 不一致"
                    )

                if name in self._tools:
                    raise ValueError(f"工具重复注册：{name}")

                description = entry.get("description")
                if not isinstance(description, str):
                    raise TypeError(f"{name} 的 description 必须是字符串")

                input_model = entry.get("input_model")
                if (
                    not isinstance(input_model, type)
                    or not issubclass(input_model, BaseModel)
                ):
                    raise TypeError(
                        f"{name} 的 input_model 必须是 Pydantic 模型类"
                    )

                if not callable(entry.get("handler")):
                    raise TypeError(f"{name} 的 handler 必须可调用")

                if "check_permissions" in entry and not callable(entry["check_permissions"]):
                    raise TypeError(f"{name} 的 check_permissions 必须可调用")

                self._tools[name] = dict(entry)

    def definitions(self):
        return [
            {
                "name": entry["name"],
                "description": entry["description"],
                "parameters": entry["input_model"].model_json_schema(),
            }
            for entry in self._tools.values()
        ]

    def execute(self, name, arguments, context):
        call_id = uuid4().hex
        started_at = monotonic()
        execution = {"started": False}
        self._audit_event(
            call_id, "request", tool_name=name,
            mode=self.permission_service.engine.settings.mode,
            workspace=str(context.workspace),
            task=text_fingerprint(context.task),
            arguments=redact_arguments(arguments),
        )
        try:
            result = self._execute(
                name, arguments, context,
                call_id=call_id, execution=execution,
            )
        except BaseException as exc:
            self._audit_event(
                call_id, "result", executed=execution["started"],
                status="interrupted", exception_type=type(exc).__name__,
                elapsed_ms=round((monotonic() - started_at) * 1000),
            )
            raise
        self._audit_event(
            call_id, "result", executed=execution["started"],
            status=(
                "succeeded" if result.ok and result.exit_code in (None, 0) else "failed"
            ) if execution["started"] else "not_executed",
            tool_ok=result.ok, exit_code=result.exit_code,
            elapsed_ms=round((monotonic() - started_at) * 1000),
        )
        return result

    def _execute(self, name, arguments, context, *, call_id, execution):
        entry = self._tools.get(name)

        if entry is None:
            self._audit_event(
                call_id, "decision", action="deny", source="registry",
                reason_code="unknown_tool",
            )
            return ToolResult(
                ok=False,
                output=f"未注册工具：{name}",
            )

        try:
            validated = entry["input_model"].model_validate(arguments)
        except ValidationError as exc:
            self._audit_event(
                call_id, "decision", action="deny", source="registry",
                reason_code="invalid_arguments",
            )
            return ToolResult(
                ok=False,
                output=f"工具参数不合法：{exc}",
            )

        execution_arguments = validated.model_dump()
        operation_key = self._repeat_guard.key(name, execution_arguments, context)
        if self._repeat_guard.blocked(operation_key):
            self._audit_event(
                call_id, "decision", action="deny", source="runtime",
                reason_code="repeated_tool_call",
            )
            return ToolResult(
                ok=False,
                output=("连续相同工具调用已执行两次，本次未执行。"
                        "请利用已有结果、调整参数或结束任务。"),
            )

        try:
            request = ToolPermissionRequest(
                tool_name=name,
                arguments=deepcopy(execution_arguments),
                workspace=str(context.workspace),
                task=context.task,
            )



            decision = self.permission_service.authorize(
                request,
                check_permissions=entry.get("check_permissions"),
            )

            if not isinstance(decision, PermissionDecision):
                raise TypeError(
                    "权限服务必须返回 PermissionDecision"
                )

            self._audit_event(
                call_id, "decision", action=decision.action,
                source=decision.source,
                reason=text_fingerprint(decision.reason),
                arguments=redact_arguments(execution_arguments),
                remember_session=getattr(decision, "remember_session", False),
            )

            if decision.action != "allow":
                return ToolResult(
                    ok=False,
                    output=f"工具未获批准：{decision.reason}",
                )

        except Exception as exc:
            self._audit_event(
                call_id, "decision", action="deny", source="error",
                exception_type=type(exc).__name__,
            )
            return ToolResult(
                ok=False,
                output=(
                    "权限处理失败，工具未执行。"
                    f"错误类型：{type(exc).__name__}"
                ),
            )
        try:
            handler = entry["handler"]
            self._audit_event(call_id, "execution_started")
            execution["started"] = True
            self._repeat_guard.record(operation_key)
            result = handler(
                **execution_arguments,
                context=context,
            )

            if isinstance(result, ToolResult):
                return result

            if isinstance(result, str):
                return ToolResult(
                    ok=True,
                    output=result,
                )

            raise TypeError(
                f"工具返回类型不支持：{type(result).__name__}"
            )

        except (ValueError, OSError, TypeError) as exc:
            return ToolResult(
                ok=False,
                output=str(exc),
            )

    def begin_turn(self):
        """新用户任务或显式恢复开始时，重置本次执行的重复计数。"""
        self._repeat_guard.reset()

    def select_tools(self, names: set[str]) -> "ToolRegistry":
        missing = names - set(self._tools)

        if missing:
            text = ", ".join(sorted(missing))
            raise ValueError(f"工具池中不存在这些工具：{text}")

        selected = {
            name: entry
            for name, entry in self._tools.items()
            if name in names
        }

        return self._copy_permission_config(self._new_registry(selected))

    def with_tools(self, *tool_groups):
        entries = dict(self._tools)
        for group in tool_groups:
            for name, entry in group.items():
                if name in entries:
                    raise ValueError(f"工具重复注册：{name}")
                entries[name] = entry
        return self._copy_permission_config(self._new_registry(entries))

    def configure_permissions(
            self,
            *,
            mode="default",
            resolver=None,
            service=None,
    ):
        if service is None:
            settings = PermissionSettings(mode=mode)

            if resolver is not None and not callable(resolver):
                raise TypeError("resolver 必须可调用")

            service = PermissionService(
                PermissionEngine(settings),
                approval=resolver,
            )

        if not isinstance(service, PermissionService):
            raise TypeError("service 必须是 PermissionService")

        self.permission_service = service

        # 保留旧接口的属性，方便已有检查脚本继续使用。
        self.permission_mode = service.engine.settings.mode
        self.permission_resolver = service.approval

    def _copy_permission_config(self, registry):
        registry.configure_permissions(
            service=self.permission_service,
        )
        registry.configure_audit(self.audit_log)
        return registry

    def configure_audit(self, audit_log: PermissionAuditLog | None):
        if audit_log is not None and not isinstance(audit_log, PermissionAuditLog):
            raise TypeError("audit_log 必须是 PermissionAuditLog")
        self.audit_log = audit_log
        self._audit_warning_shown = False

    def _audit_event(self, call_id, event, **fields):
        if self.audit_log is None:
            return
        try:
            self.audit_log.append(call_id=call_id, event=event, **fields)
        except Exception as exc:
            # 日志失败不重新执行工具，也不改变权限决定；只提示一次。
            if not self._audit_warning_shown:
                print(f"权限审计日志写入失败：{type(exc).__name__}")
                self._audit_warning_shown = True

    def _new_registry(self, entries) -> "ToolRegistry":
        return ToolRegistry(entries)
