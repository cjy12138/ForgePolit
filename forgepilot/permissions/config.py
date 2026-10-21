import os
from pathlib import Path

from forgepilot.permissions.models import (
    PermissionMode,
    PermissionRule,
    PermissionRuleFile,
    PermissionSettings,
    RuleLayer,
)


def read_rule_file(path: Path) -> list[PermissionRule]:
    if not path.exists():
        return []

    try:
        text = path.read_text(
            encoding="utf-8-sig"
        )

        document = PermissionRuleFile.model_validate_json(
            text
        )

        return document.rules

    except (OSError, ValueError) as exc:
        raise ValueError(
            f"权限配置加载失败：{path}"
        ) from exc


def permission_file_paths(
    *,
    workspace: Path,
    policy_path: Path | None = None,
    user_path: Path | None = None,
) -> dict[str, Path]:
    workspace = workspace.expanduser().resolve()

    if not workspace.is_dir():
        raise ValueError("权限配置的工作区不存在")

    if policy_path is None:
        program_data = Path(
            os.environ.get(
                "PROGRAMDATA",
                "C:/ProgramData",
            )
        )

        policy_path = (
            program_data
            / "ForgePilot"
            / "permissions.json"
        )

    if user_path is None:
        user_path = (
            Path.home()
            / ".forgepilot"
            / "permissions.json"
        )

    return {
        "policy": policy_path,
        "user": user_path,
        "project": workspace / ".forgepilot" / "permissions.json",
        "local": workspace / ".forgepilot" / "permissions.local.json",
    }


def load_permission_settings(
    *,
    workspace: Path,
    mode: PermissionMode = "default",
    cli_rules: list[PermissionRule] | None = None,
    session_rules: list[PermissionRule] | None = None,
    policy_path: Path | None = None,
    user_path: Path | None = None,
) -> PermissionSettings:
    paths = permission_file_paths(
        workspace=workspace, policy_path=policy_path, user_path=user_path,
    )

    layers = [
        RuleLayer(
            source=source,
            rules=read_rule_file(path),
        )
        for source, path in paths.items()
    ]

    layers.extend([
        RuleLayer(
            source="cli",
            rules=cli_rules if cli_rules is not None else [],
        ),
        RuleLayer(
            source="session",
            rules=(
                session_rules
                if session_rules is not None
                else []
            ),
        ),
    ])

    return PermissionSettings(
        mode=mode,
        layers=layers,
    )
