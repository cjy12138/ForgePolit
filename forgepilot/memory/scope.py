"""两个命令行入口共用的项目记忆目录规则。"""
import hashlib
import os
from pathlib import Path


def project_memory_root(
    *, project_root: Path, workspace: Path,
    owner_id: str = "local-user", project_id: str = "forgepilot-demo",
) -> Path:
    workspace = workspace.resolve()
    if workspace == (project_root / "demo_workspace").resolve():
        repository_id = "demo-repo"
    else:
        normalized = os.path.normcase(str(workspace))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        repository_id = f"repo-{digest}"
    return project_root / ".forgepilot" / "memory" / owner_id / project_id / repository_id
