import os
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.contracts import ToolContext
from forgepilot.tools.files import resolve_read_file, resolve_workspace_path


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, description="工作区内的 UTF-8 文本文件路径。")
    content: str = Field(max_length=1024 * 1024, description="完整文件内容，允许空文本。")
    overwrite: bool = Field(
        default=False,
        description="默认仅创建新文件；确认需要整文件替换时才设为 true，局部修改使用 edit_file。",
    )


def write_file(*, path: str, content: str, context: ToolContext, overwrite: bool = False):
    WriteFileInput(path=path, content=content, overwrite=overwrite)
    target = resolve_workspace_path(context, path)
    data = content.encode("utf-8")
    if len(data) > 1024 * 1024 or "\x00" in content:
        raise ValueError("仅支持不超过 1 MiB 且不含空字符的 UTF-8 文本")
    if target.exists():
        if not target.is_file():
            raise ValueError("写入目标不是普通文件")
        if not overwrite:
            raise ValueError("文件已存在；局部修改请使用 edit_file，整文件替换需 overwrite=true")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        # x 模式保证并发创建时不会覆盖已存在的目标。
        with target.open("xb") as file:
            file.write(data)
    else:
        temporary = None
        try:
            with NamedTemporaryFile(dir=target.parent, prefix=".forgepilot-write-", delete=False) as file:
                temporary = file.name
                file.write(data)
            if target.exists():
                os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    return f"已写入 {path}，UTF-8 内容 {len(data)} 字节"

class EditFileInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    path: str = Field(
        min_length=1,
        description="要编辑的工作区内文本文件，相对于工作区根目录。",
    )
    old_text: str = Field(
        min_length=1,
        description="要替换的精确原文，必须在文件中只出现一次。",
    )
    new_text: str = Field(
        description="替换后的文本；空字符串表示删除原文。",
    )

def edit_file(
        *,
        path: str,
        old_text: str,
        new_text: str,
        context: ToolContext,
):
    target = resolve_read_file(context, path)

    if target.stat().st_size > 1024*1024:
        raise ValueError("当前只支持不超过 1 MiB 的文本文件")

    with target.open("r",encoding="utf-8",newline="") as file:
        original  = file.read()

    if not old_text:
        raise ValueError("old_text 不能为空")

    first = original.find(old_text)

    if first == -1:
        raise ValueError("未找到精确匹配的原文，请重新读取文件")

    second = original.find(old_text, first + 1)

    if second != -1:
        raise ValueError(
            "原文存在多个匹配位置，请提供包含更多上下文的 old_text"
        )

    if old_text == new_text:
        raise ValueError("新旧文本相同，没有需要执行的修改")

    updated = original.replace(old_text, new_text, 1)

    with target.open("w",encoding="utf-8",newline="") as file:
        file.write(updated)

    return f"已修改 {path}，替换了 1 处文本"

def build_edit_tools():
    return {
        "write_file": {
            "name": "write_file",
            "description": (
                "创建 UTF-8 文本文件及缺失的父目录；默认不覆盖已有文件。"
                "整文件替换必须显式指定 overwrite=true；局部修改优先使用 edit_file。"
            ),
            "input_model": WriteFileInput,
            "handler": write_file,
        },
        "edit_file": {
            "name": "edit_file",
            "description": (
                "通过精确原文替换编辑已有 UTF-8 文本文件。"
                "编辑前先读取文件，old_text 必须只匹配一次。"
            ),
            "input_model": EditFileInput,
            "handler": edit_file,
        },
    }
