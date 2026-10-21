import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forgepilot.contracts import ToolContext
from forgepilot.sessions.files.index import FileContextCandidate
from forgepilot.tools.files import resolve_workspace_path


TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".md", ".txt", ".json", ".toml",
    ".yaml", ".yml", ".ini", ".cfg",
    ".css", ".html", ".sql", ".sh",
}

BLOCKED_DIRECTORIES = {
    ".git", ".ssh", ".aws", ".forgepilot",
}

@dataclass(frozen=True)
class RestoredFileContext:
    path: str
    source_result_entry_id: str
    sha256: str
    captured_at: str
    size_bytes: int
    total_chars: int
    shown_chars: int
    truncated: bool
    content: str

def read_current_file_context(
    candidate: FileContextCandidate,
    *,
    context: ToolContext,
    authorize_read: Callable[[Path], bool],
    max_file_bytes: int = 1024 * 1024,
    max_chars: int = 4000,
) -> RestoredFileContext:
    for name, value in (
            ("max_file_bytes", max_file_bytes),
            ("max_chars", max_chars),
    ):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} 必须是正整数")

    target = resolve_workspace_path(context, candidate.path)
    relative = target.relative_to(context.workspace.resolve())

    parts = {part.casefold() for part in relative.parts}
    name = target.name.casefold()

    if parts & BLOCKED_DIRECTORIES:
        raise ValueError("该目录不允许自动恢复")

    if (
            name == ".env"
            or name.startswith(".env.")
            or target.suffix.casefold() in {
        ".pem", ".key", ".p12", ".pfx",
    }
    ):
        raise ValueError("敏感文件不允许自动恢复")

    if target.suffix.casefold() not in TEXT_SUFFIXES:
        raise ValueError("当前不支持自动恢复此文件类型")

    if authorize_read(target) is not True:
        raise ValueError("当前身份没有文件读取权限")

    if not target.is_file():
        raise ValueError("目标不存在或不是普通文件")

    # 最多读取上限加一个字节，避免整文件无限增长。
    with target.open("rb") as file:
        data = file.read(max_file_bytes + 1)

    if len(data) > max_file_bytes:
        raise ValueError("文件超过自动恢复大小上限")

    if b"\x00" in data:
        raise ValueError("文件包含二进制内容")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是有效 UTF-8 文本") from exc

    shown = text[:max_chars]

    return RestoredFileContext(
        path=relative.as_posix(),
        source_result_entry_id=candidate.result_entry_id,
        sha256=hashlib.sha256(data).hexdigest(),
        captured_at=datetime.now(timezone.utc).isoformat(),
        size_bytes=len(data),
        total_chars=len(text),
        shown_chars=len(shown),
        truncated=len(shown) < len(text),
        content=shown,
    )
