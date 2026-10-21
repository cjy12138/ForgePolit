"""有界的文件检索与读取工具，不执行工作区代码。"""
import difflib
import fnmatch
import json
from pathlib import PurePosixPath
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.tools.workspace_support import checked_path, digest, output, read_bytes, read_text, walk


class PathInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(default=".", min_length=1, max_length=1000)


class FileInput(PathInput):
    path: str = Field(min_length=1, max_length=1000)


class GlobInput(PathInput):
    pattern: str = Field(default="*", min_length=1, max_length=200)
    max_results: int = Field(default=100, ge=1, le=500)


class TreeInput(PathInput):
    max_depth: int = Field(default=3, ge=1, le=12)
    max_entries: int = Field(default=200, ge=1, le=500)


class ReadFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    paths: list[str] = Field(min_length=1, max_length=8)
    per_file_chars: int = Field(default=2000, ge=1, le=5000)


class CompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    context_lines: int = Field(default=3, ge=0, le=10)


class StructuredInput(FileInput):
    pointer: str = Field(default="", max_length=1000, description="空表示全文；否则用 /a/0/b 定位，支持 ~0 和 ~1 转义。")


def glob_files(*, context, path=".", pattern="*", max_results=100):
    matches = []
    base = checked_path(context, path).relative_to(context.workspace.resolve()).as_posix()
    for name, _, directory, _ in walk(context, path):
        relative = PurePosixPath(name).relative_to(base).as_posix()
        patterns = [pattern, pattern[3:]] if pattern.startswith("**/") else [pattern]
        if not directory and any(fnmatch.fnmatchcase(relative, item) or fnmatch.fnmatchcase(relative.rsplit("/", 1)[-1], item) for item in patterns):
            matches.append(name)
            if len(matches) >= max_results:
                return output({"files": matches, "limit_reached": True})
    return output({"files": matches, "limit_reached": False})


def directory_tree(*, context, path=".", max_depth=3, max_entries=200):
    entries = []
    for name, _, directory, depth in walk(context, path, max_depth):
        entries.append({"path": name, "type": "directory" if directory else "file", "depth": depth})
        if len(entries) >= max_entries:
            return output({"entries": entries, "limit_reached": True})
    return output({"entries": entries, "limit_reached": False})


def file_info(*, context, path):
    target = checked_path(context, path)
    info = target.stat()
    return output({"path": path, "type": "directory" if target.is_dir() else "file",
                   "bytes": info.st_size, "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})


def file_hash(*, context, path):
    data = read_bytes(context, path)
    return output({"path": path, "bytes": len(data), "sha256": digest(data)})


def read_files(*, context, paths, per_file_chars=2000):
    documents = []
    remaining = 16000
    for path in paths:
        if remaining <= 0:
            break
        text = read_text(context, path)
        chunk = text[:min(per_file_chars, remaining)]
        documents.append({"path": path, "content": chunk, "truncated": len(text) > len(chunk)})
        remaining -= len(chunk)
    return output({"files": documents, "omitted_files": len(paths) - len(documents)})


def compare_files(*, context, left, right, context_lines=3):
    before, after = read_text(context, left), read_text(context, right)
    result = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=left, tofile=right, n=context_lines))
    return output(result or "文件内容相同")


def _select(document, pointer):
    if not pointer:
        return document
    if not pointer.startswith("/"):
        raise ValueError("pointer 必须为空或以 / 开头")
    try:
        for part in pointer[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(document, list):
                if not key.isdecimal():
                    raise ValueError("数组索引必须是非负整数")
                document = document[int(key)]
            elif isinstance(document, dict):
                document = document[key]
            else:
                raise ValueError("pointer 经过了非容器节点")
    except (KeyError, IndexError) as exc:
        raise ValueError("pointer 对应的节点不存在") from exc
    return document


def read_json(*, context, path, pointer=""):
    try:
        return output(_select(json.loads(read_text(context, path)), pointer))
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON 解析失败：{exc}") from exc


WORKSPACE_SPECS = {
    "glob_files": (GlobInput, glob_files, "按文件名或相对路径通配符递归查找文件；跳过隐藏、依赖目录和链接。"),
    "directory_tree": (TreeInput, directory_tree, "查看有深度和数量上限的工作区目录树。"),
    "file_info": (FileInput, file_info, "查询文件/目录类型、字节数和 UTC 修改时间。"),
    "file_hash": (FileInput, file_hash, "计算不超过 1 MiB 文件的 SHA-256，用于比对文件版本，不返回正文。"),
    "read_files": (ReadFilesInput, read_files, "一次读取最多 8 个 UTF-8 文件的有界片段。"),
    "compare_files": (CompareInput, compare_files, "比较两个 UTF-8 文件，返回有界 unified diff。"),
    "read_json": (StructuredInput, read_json, "解析 JSON，可使用 JSON Pointer 风格路径只读取相关节点。"),
}
