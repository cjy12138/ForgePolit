"""命令工具仅继承运行所需的环境项，不整包传递 Agent 的环境。"""
import os
from collections.abc import Mapping


COMMAND_ENV_ALLOWLIST = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "VIRTUAL_ENV", "JAVA_HOME", "DOTNET_ROOT",
})


def command_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    return {
        key: value for key, value in source.items()
        if key.upper() in COMMAND_ENV_ALLOWLIST
    }
