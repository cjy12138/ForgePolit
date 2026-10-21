from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from forgepilot.contracts import ToolContext
DEFAULT_READ_LIMIT = 1000

class ReadFileInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    path: str = Field(
        min_length=1,
        description="相对于工作区根目录的文本文件路径。",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="从第几个字符开始读取，从 0 开始。",
    )
    limit: int = Field(
        default=DEFAULT_READ_LIMIT,
        ge=1,
        le=20000,
        description="本次最多返回多少个字符。",
    )
    start_line: int | None = Field(
        default=None, ge=1,
        description="可选：按行读取的起始行（从 1 开始）；不与非零 offset 混用。",
    )
    end_line: int | None = Field(
        default=None, ge=1,
        description="可选：结束行（包含）；省略时最多读取 200 行。需提供 start_line。",
    )

    @model_validator(mode="after")
    def check_range(self):
        if self.start_line is None and self.end_line is not None:
            raise ValueError("end_line 需要同时提供 start_line")
        if self.start_line is not None:
            if self.offset != 0:
                raise ValueError("按行读取不能同时指定非零 offset")
            if self.end_line is not None and self.end_line < self.start_line:
                raise ValueError("end_line 不能小于 start_line")
        return self

class ListFilesInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )
    path: str = Field(
        default=".",
        min_length=1,
        description="相对于工作区的目录路径，默认列出工作区根目录。",
    )

def read_file(
    *,
    path: str,
    context: ToolContext,
    offset: int = 0,
    limit: int = DEFAULT_READ_LIMIT,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    ReadFileInput(
        path=path, offset=offset, limit=limit,
        start_line=start_line, end_line=end_line,
    )
    target = resolve_read_file(context,path)

    # 本阶段先限制整文件读取量，之后再研究更大的文件。
    if target.stat().st_size > 1024 * 1024:
        raise ValueError("当前只支持不超过 1 MiB 的文本文件")

    with target.open("r", encoding="utf-8", newline="") as file:
        content = file.read()

    if "\x00" in content:
        raise ValueError("目标包含空字符，不作为文本文件读取")

    if start_line is not None:
        lines = content.splitlines(keepends=True)
        if start_line > max(1, len(lines)):
            raise ValueError("start_line 超出了文件行数")
        last = min(end_line or start_line + 199, len(lines))
        offset = sum(map(len, lines[:start_line - 1]))
        requested = "".join(lines[start_line - 1:last])
        chunk = requested[:limit]
        end = offset + len(chunk)
        numbered = "".join(
            f"{number}: {line}"
            for number, line in enumerate(chunk.splitlines(keepends=True), start_line)
        )
        continuation = (
            f"yes - call read_file again with offset {end} (character mode)"
            if len(chunk) < len(requested) else "no"
        )
        return (
            f"FILE: {path}\nSTART_LINE: {start_line}\n"
            f"REQUESTED_END_LINE: {last}\nTOTAL_LINES: {len(lines)}\n"
            f"OFFSET: {offset}\nEND: {end}\n"
            f"TRUNCATED: {continuation}\n"
            f"MORE_FILE: {'yes' if end < len(content) else 'no'}\n\n"
            + numbered
        )

    if offset > len(content):
        raise ValueError("offset 超出了文件长度")
    # 本次读取的结束位置不能超过文件长度。
    end = min(offset + limit, len(content))

    # 从 offset 开始，读取到 end 之前。
    chunk = content[offset:end]

    # 如果后面还有字符，则说明尚未读完。
    truncated = end < len(content)

    continuation = (
        f"yes - call read_file again with offset {end}"
        if truncated
        else "no"
    )

    header = (
        f"FILE: {path}\n"
        f"OFFSET: {offset}\n"
        f"END: {end}\n"
        f"TOTAL_CHARS: {len(content)}\n"
        f"TRUNCATED: {continuation}\n\n"
    )

    return header + chunk



def build_file_tools():
    return {
        "read_file": {
            "name": "read_file",
            "description": (
                "读取工作区内的 UTF-8 文本文件，"
                "支持通过 offset 和 limit 分段读取。"
                "也可指定 start_line/end_line 按行读取并显示行号；"
                "编辑时不要将行号前缀放入 old_text。"
            ),
            "input_model": ReadFileInput,
            "handler": read_file,
        },
        "list_files": {
            "name": "list_files",
            "description": (
                "列出工作区内指定目录的直接子项，"
                "区分文件和目录；不递归，最多返回 200 项。"
            ),
            "input_model": ListFilesInput,
            "handler": list_files,
        },
    }

def resolve_workspace_path(context: ToolContext, path: str) -> Path:
    relative = Path(path)

    if relative.anchor:
        raise ValueError("请使用相对于工作区的路径")

    workspace = context.workspace.resolve()
    target = (workspace / relative).resolve()

    if not target.is_relative_to(workspace):
        raise ValueError("路径不在允许的工作区内")

    return target

def resolve_read_file(context: ToolContext, path: str) -> Path:
    target = resolve_workspace_path(context, path)

    if not target.is_file():
        raise ValueError("目标不存在或不是普通文件")

    return target

def list_files(
        *,
        context: ToolContext,
        path: str = '.',
) -> str:
    target = resolve_workspace_path(context, path)
    if not target.is_dir():
        raise ValueError("目标不存在或不是目录")

    entries = sorted(
        target.iterdir(),
        key=lambda entry: entry.name.casefold(),
    )

    lines = []

    for entry in entries[:200]:
        # 符号链接单独标记，不在这里跟随它读取内容。
        if entry.is_symlink():
            kind = "link"
        elif entry.is_dir():
            kind = "dir"
        else:
            kind = "file"

        lines.append(f"{kind} {entry.name}")

    output = "\n".join(lines) or "(empty)"

    if len(entries) > 200:
        output += "\nTRUNCATED: 仅显示前 200 项，请缩小目录范围。"

    return output
