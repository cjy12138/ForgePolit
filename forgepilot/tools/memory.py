import hashlib
import json
from functools import partial

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.contracts import ToolContext
from forgepilot.memory.store import MarkdownMemoryStore

from fnmatch import fnmatchcase

class ReadMemoryInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    filename: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}\.md$",
        description="记忆索引或记忆搜索工具返回的主题文件名。",
    )

    offset: int = Field(
        default=0,
        ge=0,
        description="正文起始字符位置，从 0 开始。",
    )

    limit: int = Field(
        default=4000,
        ge=1,
        le=8000,
        description="最多返回的正文字符数。",
    )

def read_memory(
    *,
    filename: str,
    context: ToolContext,
    memory_store: MarkdownMemoryStore,
    offset: int = 0,
    limit: int = 4000,
    consume_read=None,
) -> str:
    params = ReadMemoryInput(
        filename=filename,
        offset=offset,
        limit=limit,
    )

    document = memory_store.read(params.filename)
    content = document.content

    if params.offset > len(content):
        raise ValueError("offset 超出记忆正文长度")

    end = min(params.offset + params.limit, len(content))
    chunk = content[params.offset:end]
    if consume_read is not None and chunk:
        chunk = consume_read(chunk)
        end = params.offset + len(chunk)

    # 元数据或正文改变，哈希都会改变。
    serialized = json.dumps(
        document.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
    )
    document_sha256 = hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()

    return json.dumps(
        {
            "filename": document.filename,
            "metadata": document.metadata.model_dump(),
            "document_sha256": document_sha256,
            "offset": params.offset,
            "end": end,
            "total_chars": len(content),
            "next_offset": (
                end if end < len(content) else None
            ),
            "content": chunk,
        },
        ensure_ascii=False,
    )


class GlobMemoryInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    pattern: str = Field(
        default="*.md",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9_*?.\[\]!-]+$",
        description=(
            "主题文件名匹配模式，例如 *.md、feedback_*.md。"
            "不支持目录路径或递归搜索。"
        ),
    )

    limit: int = Field(
        default=30,
        ge=1,
        le=100,
        description="最多返回的文件数量。",
    )


def glob_memory(
    *,
    context: ToolContext,
    memory_store: MarkdownMemoryStore,
    pattern: str = "*.md",
    limit: int = 30,
) -> str:
    params = GlobMemoryInput(
        pattern=pattern,
        limit=limit,
    )

    matches = [
        filename
        for filename in memory_store.list_filenames()
        if fnmatchcase(filename, params.pattern)
    ]

    return json.dumps(
        {
            "pattern": params.pattern,
            "files": matches[:params.limit],
            "total_matches": len(matches),
            "truncated": len(matches) > params.limit,
        },
        ensure_ascii=False,
    )

class GrepMemoryInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    keyword: str = Field(
        min_length=1,
        max_length=100,
        description="要搜索的字面关键词，不使用正则表达式。",
    )

    file_pattern: str = Field(
        default="*.md",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9_*?.\[\]!-]+$",
        description="搜索的主题文件名模式，不支持目录路径。",
    )

    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="最多返回的匹配行数。",
    )

def grep_memory(
    *,
    keyword: str,
    context: ToolContext,
    memory_store: MarkdownMemoryStore,
    file_pattern: str = "*.md",
    limit: int = 20,
) -> str:
    params = GrepMemoryInput(
        keyword=keyword,
        file_pattern=file_pattern,
        limit=limit,
    )

    matches = []
    truncated = False

    for filename in memory_store.list_filenames():
        if not fnmatchcase(filename, params.file_pattern):
            continue

        text = memory_store.read_text(filename)

        for line_number, line in enumerate(
            text.splitlines(),
            start=1,
        ):
            position = line.find(params.keyword)

            if position == -1:
                continue

            # 多发现一条，才能确认确实存在未返回的结果。
            if len(matches) >= params.limit:
                truncated = True
                break

            # 保留关键词附近的内容，避免整行过长。
            start = max(0, position - 80)
            end = min(len(line), start + 240)

            matches.append({
                "filename": filename,
                "line_number": line_number,
                "snippet": line[start:end],
                "snippet_truncated": (
                    start > 0 or end < len(line)
                ),
            })

        if truncated:
            break

    return json.dumps(
        {
            "keyword": params.keyword,
            "file_pattern": params.file_pattern,
            "matches": matches,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )

def build_memory_tools(
    *,
    memory_store: MarkdownMemoryStore,
    consume_read=None,
):
    return {
        "read_memory": {
            "name": "read_memory",
            "description": (
                "根据主题文件名，分段读取"
                "当前范围内的长期记忆正文。"
                "记忆可能过期，涉及当前代码的事实需要重新验证。"
            ),
            "input_model": ReadMemoryInput,
            "handler": partial(
                read_memory,
                memory_store=memory_store,
                consume_read=consume_read,
            ),
        },
        "glob_memory": {
            "name": "glob_memory",
            "description": (
                "按文件名模式查找当前范围内的长期记忆主题。"
                "包含未列入 MEMORY.md 索引的文件。"
                "只返回文件名，不读取正文。"
            ),
            "input_model": GlobMemoryInput,
            "handler": partial(
                glob_memory,
                memory_store=memory_store,
            ),
        },
        "grep_memory": {
            "name": "grep_memory",
            "description": (
                "按字面关键词搜索当前范围内长期记忆的"
                "元数据和正文，返回文件名、行号及有限片段。"
                "包含未列入 MEMORY.md 索引的主题。"
                "找到相关主题后，可调用 read_memory 读取正文。"
            ),
            "input_model": GrepMemoryInput,
            "handler": partial(
                grep_memory,
                memory_store=memory_store,
            ),
        },
    }
