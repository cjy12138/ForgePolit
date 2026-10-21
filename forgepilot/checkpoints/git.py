import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import re

@dataclass(frozen=True)
class GitCheckpoint:
    id: str
    commit_id: str
    base_commit_id: str
    ref: str

@dataclass(frozen=True)
class GitRestorePlan:
    target: GitCheckpoint
    backup: GitCheckpoint
    changes: str
    patch: str

class GitCheckpointStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

        if not self.workspace.is_dir():
            raise ValueError("工作区不存在")

        root = Path(self._git(
            ["rev-parse", "--show-toplevel"]
        )).resolve()

        if root != self.workspace:
            raise ValueError("检查点工作区必须是 Git 仓库根目录")

    def _git(
            self,
            args: list[str],
            *,
            env: dict[str, str] | None = None,
            strip_output: bool = True,
    ) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.workspace), *args],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"Git 命令失败：{detail or result.returncode}"
            )

        return (
            result.stdout.strip()
            if strip_output
            else result.stdout
        )

    def _capture_current_tree(self) -> tuple[str, str]:
        # 本阶段要求仓库至少已有一次提交。
        base_commit_id = self._git(
            ["rev-parse", "--verify", "HEAD"]
        )

        paths = self._git(
            [
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            strip_output=False,
        ).split("\0")

        for path in paths:
            name = Path(path).name

            if (
                    name == ".env"
                    or (
                    name.startswith(".env.")
                    and name != ".env.example"
            )
            ):
                raise ValueError(
                    "检查点范围包含 .env 文件，请先移出跟踪范围并忽略"
                )

        with TemporaryDirectory(
                prefix="forgepilot-index-"
        ) as directory:
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(
                Path(directory) / "index"
            )

            self._git(
                ["read-tree", base_commit_id],
                env=env,
            )

            self._git(
                ["add", "-A", "--", "."],
                env=env,
            )

            tree_id = self._git(
                ["write-tree"],
                env=env,
            )

        return base_commit_id, tree_id

    def _save_tree(
            self,
            base_commit_id: str,
            tree_id: str,
    ) -> GitCheckpoint:
        checkpoint_id = uuid4().hex
        ref = f"refs/forgepilot/checkpoints/{checkpoint_id}"

        commit_id = self._git([
            "-c", "user.name=ForgePilot",
            "-c", "user.email=forgepilot@local.invalid",
            "commit-tree",
            tree_id,
            "-p", base_commit_id,
            "-m", f"ForgePilot checkpoint {checkpoint_id}",
        ])

        self._git([
            "update-ref",
            ref,
            commit_id,
        ])

        return GitCheckpoint(
            id=checkpoint_id,
            commit_id=commit_id,
            base_commit_id=base_commit_id,
            ref=ref,
        )

    def create(self) -> GitCheckpoint:
        base_commit_id, tree_id = self._capture_current_tree()

        return self._save_tree(
            base_commit_id,
            tree_id,
        )

    def create_if_changed(
            self,
            previous_commit_id: str | None,
    ) -> GitCheckpoint | None:
        base_commit_id, tree_id = self._capture_current_tree()

        if previous_commit_id is not None:
            previous_tree_id = self._git([
                "rev-parse",
                "--verify",
                f"{previous_commit_id}^{{tree}}",
            ])

            if tree_id == previous_tree_id:
                return None

        return self._save_tree(
            base_commit_id,
            tree_id,
        )
    def get(self, checkpoint_id: str) -> GitCheckpoint:
        if re.fullmatch(r"[0-9a-f]{32}", checkpoint_id) is None:
            raise ValueError("检查点 ID 必须是 32 位小写十六进制字符串")

        ref = f"refs/forgepilot/checkpoints/{checkpoint_id}"

        commit_id = self._git([
            "rev-parse",
            "--verify",
            f"{ref}^{{commit}}",
        ])

        base_commit_id = self._git([
            "rev-parse",
            "--verify",
            f"{commit_id}^",
        ])

        return GitCheckpoint(
            id=checkpoint_id,
            commit_id=commit_id,
            base_commit_id=base_commit_id,
            ref=ref,
        )

    def prepare_restore(
        self,
        checkpoint_id: str,
        *,
        expected_commit_id: str | None = None,
    ) -> GitRestorePlan:
        target = self.get(checkpoint_id)

        if (
            expected_commit_id is not None
            and target.commit_id != expected_commit_id
        ):
            raise ValueError("Git 引用与会话保存的检查点提交不一致")

        # 当前代码先保存为备份，不关联新的会话节点。
        backup = self.create()

        common_args = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
        ]

        changes = self._git([
            *common_args,
            "--name-status",
            backup.commit_id,
            target.commit_id,
            "--",
        ])

        patch = self._git([
            *common_args,
            backup.commit_id,
            target.commit_id,
            "--",
        ])

        return GitRestorePlan(
            target=target,
            backup=backup,
            changes=changes,
            patch=patch,
        )

    def _tree_files(self, commit_id: str) -> set[str]:
        output = self._git(
            ["ls-tree", "-r", "-z", commit_id],
            strip_output=False,
        )

        files = set()

        for item in output.split("\0"):
            if not item:
                continue

            metadata, name = item.split("\t", 1)
            mode, object_type, _ = metadata.split()

            # 第一版处理普通文件；特殊文件不直接恢复。
            if (
                object_type != "blob"
                or mode not in {"100644", "100755"}
            ):
                raise ValueError(
                    "当前恢复流程不支持符号链接或子模块"
                )

            files.add(name)

        return files

    def _checked_restore_path(self, name: str) -> Path:
        relative = Path(name)

        if (
            relative.is_absolute()
            or not relative.parts
            or any(
                part.lower() in {".git", ".forgepilot"}
                for part in relative.parts
            )
        ):
            raise ValueError("恢复路径包含受保护目录")

        filename = relative.name.lower()

        if (
            filename == ".env"
            or (
                filename.startswith(".env.")
                and filename != ".env.example"
            )
        ):
            raise ValueError("不能恢复或移除 .env 文件")

        path = self.workspace / relative

        # 避免通过链接目录操作其他位置。
        for parent in path.parents:
            if parent == self.workspace:
                break

            if (
                parent.is_symlink()
                or (parent.exists() and not parent.is_dir())
            ):
                raise ValueError("恢复路径的父目录存在链接或文件冲突")

        if path.is_symlink():
            raise ValueError("恢复路径是符号链接")

        resolved = path.resolve()

        if (
            resolved == self.workspace
            or not resolved.is_relative_to(self.workspace)
        ):
            raise ValueError("恢复路径不在工作区内")

        if resolved.exists() and not resolved.is_file():
            raise ValueError("恢复路径存在文件与目录冲突")

        return resolved

    def restore(self, plan: GitRestorePlan) -> GitCheckpoint:
        target = self.get(plan.target.id)
        backup = self.get(plan.backup.id)

        if (
            target.commit_id != plan.target.commit_id
            or backup.commit_id != plan.backup.commit_id
        ):
            raise ValueError("检查点引用已经变化，请重新预览")

        # 再保存一次当前状态，用文件树确认预览没有过期。
        current = self.create()

        current_tree = self._git([
            "rev-parse",
            f"{current.commit_id}^{{tree}}",
        ])
        expected_tree = self._git([
            "rev-parse",
            f"{backup.commit_id}^{{tree}}",
        ])

        if current_tree != expected_tree:
            raise ValueError("预览后代码已变化，请重新预览")

        current_files = self._tree_files(current.commit_id)
        target_files = self._tree_files(target.commit_id)

        checked_paths = {
            name: self._checked_restore_path(name)
            for name in current_files | target_files
        }

        for name in target_files:
            path = checked_paths[name]

            if path.exists() and name not in current_files:
                # 例如一个被忽略的文件占用了目标路径，
                # 它没有进入备份，不能直接覆盖。
                raise ValueError("目标路径存在未被备份的文件")

        with TemporaryDirectory(
            prefix="forgepilot-restore-index-"
        ) as directory:
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(
                Path(directory) / "index"
            )

            self._git(
                ["read-tree", target.commit_id],
                env=env,
            )

            self._git(
                ["checkout-index", "--all", "--force"],
                env=env,
            )

        # checkout-index 负责写入，不负责移除多余文件。
        for name in sorted(current_files - target_files):
            path = checked_paths[name]

            if path.exists():
                path.unlink()

        return target

