"""扩展文件工具共用边界、容量限制和遍历逻辑。"""
import hashlib
import json
import os
from pathlib import Path

from forgepilot.permissions.boundaries import check_path_boundary
from forgepilot.permissions.models import ToolPermissionRequest
from forgepilot.tools.files import resolve_workspace_path

MAX_FILE_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 20000
MAX_VISITED = 10000
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


def checked_path(context, path):
    result = check_path_boundary(ToolPermissionRequest(
        tool_name="read_file", arguments={"path": path}, workspace=str(context.workspace),
    ))
    if result.action == "deny":
        raise ValueError(result.reason)
    return resolve_workspace_path(context, path.replace("\\", "/"))


def read_bytes(context, path):
    target = checked_path(context, path)
    if not target.is_file():
        raise ValueError("目标不存在或不是普通文件")
    with target.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("仅支持不超过 1 MiB 的文件")
    return data


def read_text(context, path):
    try:
        text = read_bytes(context, path).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("目标不是 UTF-8 文本") from exc
    if "\x00" in text:
        raise ValueError("目标包含空字符，不作为文本读取")
    return text


def digest(data):
    return hashlib.sha256(data).hexdigest()


def output(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\nTRUNCATED: 输出超过 20000 字符，请缩小范围。"
    return text


def walk(context, path=".", max_depth=8):
    """不跟随链接/junction，不遍历隐藏目录或常见依赖目录；有节点上限。"""
    root = checked_path(context, path)
    if not root.is_dir():
        raise ValueError("目标不是目录")
    visited = 0
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_VISITED:
                    raise ValueError("遍历超过 10000 项，请缩小目录范围")
                if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                    continue
                target = Path(entry.path)
                if entry.is_symlink() or target.is_junction():
                    continue
                relative = target.relative_to(context.workspace.resolve()).as_posix()
                try:
                    checked_path(context, relative)
                except ValueError:
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                yield relative, target, is_dir, depth + 1
                if is_dir and depth + 1 < max_depth:
                    stack.append((target, depth + 1))
