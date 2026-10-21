import subprocess

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.contracts import ToolContext
from forgepilot.tools.files import resolve_workspace_path
from forgepilot.environments.variables import command_environment

class GrepFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)

    pattern: str = Field(
        min_length=1,
        description="要搜索的文本，按字面内容匹配，不是正则表达式。",
    )
    path: str = Field(
        default=".",
        min_length=1,
        description="工作区内的文件或目录，默认搜索整个工作区。",
    )

def grep_files(
    *,
    pattern: str,
    context: ToolContext,
    path: str = ".",
) -> str:
    workspace = context.workspace.resolve()
    target = resolve_workspace_path(context, path)

    if not target.exists():
        raise ValueError("搜索目标不存在")

    relative = target.relative_to(workspace)

    try:
        result = subprocess.run(
            [
                "rg",
                "--line-number",
                "--with-filename",
                "--no-heading",
                "--color=never",
                "--fixed-strings",
                "-e",
                pattern,
                "--",
                str(relative),
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,
                env=command_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("搜索超时，请缩小搜索范围") from exc

    # 根据 returncode 区分匹配、无匹配和执行失败。
    if result.returncode == 0:
        return result.stdout

    if result.returncode == 1:
        return "(no matches)"

    raise ValueError(
        f"搜索执行失败：{result.stderr.strip() or result.returncode}"
    )

def build_search_tools():
    return {
        "grep_files": {
            "name": "grep_files",
            "description": (
                "在工作区文件或目录中搜索字面文本，返回文件名、行号和匹配行。"
                "默认遵循 ripgrep 的忽略规则，不搜索隐藏文件。"
            ),
            "input_model": GrepFilesInput,
            "handler": grep_files,
        }
    }
