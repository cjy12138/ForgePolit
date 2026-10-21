import hashlib
import json
import os
from pathlib import Path

from forgepilot.permissions.models import ToolPermissionRequest


def hash_permission_operation(request: ToolPermissionRequest) -> str:
    data = {
        "tool_name": request.tool_name,
        "workspace": os.path.normcase(str(Path(request.workspace).resolve())),
        "arguments": request.arguments,
    }
    text = json.dumps(
        data, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
