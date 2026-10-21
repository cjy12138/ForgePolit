from pathlib import Path
from forgepilot.paths import ensure_demo_workspace

from forgepilot.contracts import ToolContext
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.search import build_search_tools

workspace = ensure_demo_workspace()
context = ToolContext(workspace=workspace)


registry = ToolRegistry({
    **build_file_tools(),
    **build_search_tools(),
})



for arguments in [
    {"pattern": "cde"},
    {"pattern": "不存在的文本"},
    {"pattern": "abc", "path": ".."},
]:
    result = registry.execute(
        "grep_files",
        arguments,
        context=context,
    )

    print(arguments, "=>", result.ok)
    print(result.output)

