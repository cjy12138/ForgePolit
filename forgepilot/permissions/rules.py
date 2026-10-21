import os
import shlex
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from forgepilot.permissions.operation import hash_permission_operation
from forgepilot.tools.extended_catalog import EXTENDED_PATH_FIELDS

from forgepilot.permissions.models import (
    PermissionRule,
    PermissionSettings,
    RuleSource,
    SOURCE_PRIORITY,
    ToolCheckResult,
    ToolPermissionRequest,
)


PATH_TOOLS = {
    "read_file",
    "list_files",
    "grep_files",
    "edit_file",
    "write_file",
}


class RuleCheckResult(ToolCheckResult):
    rule_source: RuleSource | None = None
    matched_rule: PermissionRule | None = None


def normalize_path_pattern(value: str) -> str:
    normalized = PurePosixPath(
        value.replace("\\", "/")
    ).as_posix()

    if os.name == "nt":
        normalized = normalized.casefold()

    return normalized


def rule_matches(
    rule: PermissionRule,
    request: ToolPermissionRequest,
) -> bool:
    if not fnmatchcase(request.tool_name, rule.tool):
        return False

    if (
        rule.operation_sha256 is not None
        and rule.operation_sha256 != hash_permission_operation(request)
    ):
        return False

    # 指纹检查通过后，再匹配可选的内容模式。
    if rule.pattern is None:
        return True

    arguments = request.arguments

    if request.tool_name in EXTENDED_PATH_FIELDS:
        values = []
        for field in EXTENDED_PATH_FIELDS[request.tool_name]:
            value = arguments.get(field, ".")
            values.extend(value if isinstance(value, list) else [value])
        matches = [isinstance(value, str) and fnmatchcase(
            normalize_path_pattern(value), normalize_path_pattern(rule.pattern),
        ) for value in values]
        # 多路径允许规则要求全部匹配，禁止/确认规则命中任一路径即生效。
        return bool(matches) and (all(matches) if rule.action == "allow" else any(matches))

    if request.tool_name in PATH_TOOLS:
        value = arguments.get("path", ".")

        if not isinstance(value, str):
            return False

        return fnmatchcase(
            normalize_path_pattern(value),
            normalize_path_pattern(rule.pattern),
        )

    if request.tool_name == "run_command":
        value = arguments.get("command")

    elif request.tool_name == "run_python":
        args = arguments.get("args")

        if (
            not isinstance(args, list)
            or not args
            or any(not isinstance(arg, str) for arg in args)
        ):
            return False

        # 仅生成统一的匹配文本，不执行 shell。
        value = shlex.join(["python", *args])

    else:
        # 记忆主题工具可以按 filename 匹配。
        # 没有支持的内容字段时，不匹配带 pattern 的规则。
        value = arguments.get("filename")

    return (
        isinstance(value, str)
        and fnmatchcase(value, rule.pattern)
    )


class RuleEngine:
    def __init__(self, settings: PermissionSettings):
        self.settings = settings.model_copy(deep=True)

    def evaluate(
        self,
        request: ToolPermissionRequest,
    ) -> RuleCheckResult:
        layers = sorted(
            self.settings.layers,
            key=lambda layer: SOURCE_PRIORITY[layer.source],
        )

        matches = [
            (layer.source, rule)
            for layer in layers
            for rule in layer.rules
            if rule_matches(rule, request)
        ]

        # 所有来源中的 deny 都优先，不能被 allow 覆盖。
        for source, rule in matches:
            if rule.action == "deny":
                return RuleCheckResult(
                    action="deny",
                    reason=f"命中 {source} 来源的拒绝规则。",
                    rule_source=source,
                    matched_rule=rule,
                )

        # 没有 deny，按配置来源优先级决定。
        for layer in layers:
            layer_matches = [
                rule
                for source, rule in matches
                if source == layer.source
            ]

            if not layer_matches:
                continue

            # 同一来源内 ask 优先于 allow。
            selected = next(
                (
                    rule
                    for rule in layer_matches
                    if rule.action == "ask"
                ),
                layer_matches[0],
            )

            if selected.action == "ask":
                return RuleCheckResult(
                    action="abstain",
                    reason=(
                        f"命中 {layer.source} 来源的 ask 规则，"
                        "继续后续权限检查。"
                    ),
                    rule_source=layer.source,
                    matched_rule=selected,
                )

            return RuleCheckResult(
                action="allow",
                reason=f"命中 {layer.source} 来源的允许规则。",
                rule_source=layer.source,
                matched_rule=selected,
            )

        return RuleCheckResult(
            action="abstain",
            reason="没有命中规则，继续后续权限检查。",
        )
