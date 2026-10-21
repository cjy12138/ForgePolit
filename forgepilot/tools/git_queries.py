"""只读 Git 查询。固定子命令、参数数组、禁用外部 diff/textconv 和分页器。"""
import re
import os
import subprocess
from tempfile import TemporaryFile

from pydantic import Field

from forgepilot.environments.variables import command_environment
from forgepilot.permissions.boundaries import contains_protected_part
from forgepilot.tools.workspace import FileInput, PathInput
from forgepilot.tools.workspace_support import checked_path, output


class GitLogInput(PathInput):
    limit: int = Field(default=10, ge=1, le=50)


class GitDiffInput(FileInput):
    staged: bool = False


class GitFileInput(FileInput):
    ref: str = Field(default="HEAD", min_length=1, max_length=200)


def _git(context, args, *, binary=False):
    env = command_environment()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_LITERAL_PATHSPECS": "1",
                "GIT_CONFIG_GLOBAL": "NUL" if os.name == "nt" else "/dev/null"})
    try:
        # 输出先落临时文件，避免将大型仓库输出全部放进内存；最多读回 64 KiB。
        with TemporaryFile() as stdout, TemporaryFile() as stderr:
            result = subprocess.run(
                ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", *args],
                cwd=context.workspace, env=env, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, timeout=15, shell=False,
            )
            stdout.seek(0)
            data = stdout.read(65537)
            stderr.seek(0)
            error = stderr.read(2000).decode("utf-8", errors="replace")
        if result.returncode:
            raise ValueError(f"Git 查询失败：{error.strip() or result.returncode}")
        if binary:
            if len(data) > 65536:
                raise ValueError("Git 列表超过 64 KiB，请缩小路径范围")
            return data
        text = data[:65536].decode("utf-8", errors="replace")
        return text + ("\nTRUNCATED: Git 输出超过 64 KiB" if len(data) > 65536 else "")
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git 查询超过 15 秒，请缩小范围") from exc


def _root(context):
    from pathlib import Path
    root = Path(_git(context, ["rev-parse", "--show-toplevel"]).strip()).resolve()
    if root != context.workspace.resolve():
        raise ValueError("当前工作区必须是 Git 仓库根目录，拒绝读取上级仓库")


def _path(context, path):
    target = checked_path(context, path)
    return target.relative_to(context.workspace.resolve()).as_posix()


def _visible(context, name):
    try:
        if contains_protected_part(tuple(name.replace("\\", "/").split("/"))):
            return False
        checked_path(context, name)
        return True
    except (ValueError, OSError):
        return False


def _commit(context, ref):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}", ref) or ".." in ref:
        raise ValueError("ref 只支持提交哈希或普通分支/标签名，不接受选项和 revision 表达式")
    return _git(context, ["rev-parse", "--verify", ref + "^{commit}"]).strip()


def git_status(*, context, path="."):
    _root(context)
    path = _path(context, path)
    data = _git(context, ["status", "--porcelain=v1", "-z", "--untracked-files=normal", "--no-renames", "--", path], binary=True)
    items = []
    for entry in data.decode("utf-8", errors="replace").split("\x00"):
        if entry and _visible(context, entry[3:]):
            items.append({"status": entry[:2], "path": entry[3:]})
    return output({"entries": items[:300], "limit_reached": len(items) > 300})


def git_diff(*, context, path, staged=False):
    _root(context)
    path = _path(context, path)
    if path == "." or checked_path(context, path).is_dir():
        raise ValueError("git_diff 只接受单个文件；先用 git_status 找到具体路径")
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
    if staged:
        args.append("--cached")
    return output(_git(context, [*args, "--", path]) or "(no diff)")


def git_log(*, context, path=".", limit=10):
    _root(context)
    path = _path(context, path)
    return output(_git(context, ["log", "--no-show-signature", f"--max-count={limit}", "--format=%h %aI %s", "--", path]))


def git_show_file(*, context, path, ref="HEAD"):
    _root(context)
    path = _path(context, path)
    commit = _commit(context, ref)
    tree = _git(context, ["ls-tree", "-z", commit, "--", path], binary=True).decode("utf-8", errors="replace")
    if not tree.startswith(("100644 blob ", "100755 blob ")):
        raise ValueError("指定提交中不存在该普通文件")
    text = _git(context, ["show", f"{commit}:{path}"])
    if "\x00" in text:
        raise ValueError("历史文件包含二进制内容，不作为文本展示")
    return output(text)


GIT_SPECS = {
    "git_status": (PathInput, git_status, "查询暂存区/工作区状态，过滤受保护路径；不修改 HEAD 或索引。"),
    "git_diff": (GitDiffInput, git_diff, "查看单文件未暂存或已暂存差异；不运行外部 diff/textconv。"),
    "git_log": (GitLogInput, git_log, "查看最多 50 条提交的哈希、时间与标题，可按路径过滤。"),
    "git_show_file": (GitFileInput, git_show_file, "读取某提交的普通文本文件，不 checkout、不恢复文件。"),
}
