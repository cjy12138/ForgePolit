"""小型 JSONL 审计：默认不保存自由文本正文，不参与权限放行。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock


def text_fingerprint(value: str) -> dict:
    return {
        "redacted": True,
        "chars": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest(),
    }


def redact_arguments(value, *, depth=0):
    if depth >= 8:
        return "[DEPTH_LIMIT]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:50]:
            if not isinstance(key, str):
                continue
            if any(word in key.casefold() for word in (
                "password", "secret", "token", "api_key", "apikey", "authorization",
            )):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_arguments(item, depth=depth + 1)
        if len(value) > 50:
            result["_truncated"] = True
        return result
    if isinstance(value, (list, tuple)):
        result = [redact_arguments(item, depth=depth + 1) for item in value[:50]]
        if len(value) > 50:
            result.append("[TRUNCATED]")
        return result
    if isinstance(value, str):
        return text_fingerprint(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSUPPORTED_VALUE]"


class PermissionAuditLog:
    def __init__(self, path: Path, *, session_id: str, task_id: str | None = None):
        self.path = Path(path)
        self.session_id = session_id
        self.task_id = task_id

    def append(self, *, call_id: str, event: str, **fields):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "task_id": self.task_id,
            "call_id": call_id,
            "event": event,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=1):
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line)
