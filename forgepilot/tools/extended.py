"""统一装配扩展工具，沿用 Registry 的审计、权限与 ToolResult 协议。"""
from forgepilot.permissions.tool_checks import ToolSafetyChecker
from forgepilot.tools.code_inspection import CODE_SPECS
from forgepilot.tools.extended_catalog import EXTENDED_TOOL_NAMES
from forgepilot.tools.git_queries import GIT_SPECS
from forgepilot.tools.workspace import WORKSPACE_SPECS


def build_extended_tools():
    specs = {**WORKSPACE_SPECS, **CODE_SPECS, **GIT_SPECS}
    if set(specs) != EXTENDED_TOOL_NAMES:
        raise ValueError("扩展工具目录与实现不一致")
    checker = ToolSafetyChecker()
    return {
        name: {"name": name, "description": description, "input_model": model,
               "handler": handler, "check_permissions": checker.check}
        for name, (model, handler, description) in specs.items()
    }
