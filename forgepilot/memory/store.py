import re
import os
import hashlib
from tempfile import TemporaryDirectory
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import json
import yaml
from contextlib import contextmanager

from filelock import FileLock
from forgepilot.memory.index import (
    MAX_INDEX_BYTES, MAX_INDEX_ENTRIES, MemoryIndexChange, MemoryIndexState,
)

from forgepilot.memory.models import (
    MemoryDocument,
    MemoryMetadata,
)

class MarkdownMemoryStore:
    def __init__(self, root: Path):
        # root 由程序根据用户、项目和仓库范围构造。
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_lock = FileLock(
            str(self.root / ".memory.lock")
        )

    def _topic_path(self, filename: str) -> Path:
        if re.fullmatch(
                r"[a-z][a-z0-9_]{0,63}\.md",
                filename,
        ) is None:
            raise ValueError("记忆文件名不合法")

        target = self.root / filename

        if target.is_symlink():
            raise ValueError("记忆文件不能是符号链接")

        if not target.resolve().is_relative_to(self.root):
            raise ValueError("记忆文件不在允许的目录内")

        return target

    def _serialize_document(self, document: MemoryDocument) -> str:
        # 再次校验，避免调用者修改对象后绕过限制。
        document = MemoryDocument.model_validate(
            document.model_dump()
        )

        metadata = yaml.safe_dump(
            document.metadata.model_dump(
                exclude={"previous_sources"}
            ),
            allow_unicode=True,
            sort_keys=False,
            width=100000,
        )

        if document.metadata.previous_sources:
            history = json.dumps(
                [
                    source.model_dump()
                    for source in document.metadata.previous_sources
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )

            history_line = f"previous_sources: {history}\n"

            if len(history_line.encode("utf-8")) > 4096:
                raise ValueError("旧来源记录过长，需要整理主题")

            metadata += history_line

        header = "---\n" + metadata + "---\n"

        if len(header.splitlines()) > 30:
            raise ValueError("记忆 frontmatter 超过 30 行")

        if any(
                len(line.encode("utf-8")) > 4096
                for line in header.splitlines(keepends=True)
        ):
            raise ValueError("记忆 frontmatter 单行超过 4096 字节")

        text = (
                "---\n"
                + metadata
                + "---\n\n"
                + document.content
                + "\n"
        )

        return text

    def _save_unlocked(self, document: MemoryDocument) -> None:
        self.apply_changes([document])

    def save(self, document: MemoryDocument) -> None:
        with self.write_lock():
            self._save_unlocked(document)

    def read(self, filename: str) -> MemoryDocument:
        text = self.read_text(filename)
        return self.parse_document(filename, text)

    @staticmethod
    def parse_document(filename: str, text: str) -> MemoryDocument:
        # 解析旧 Markdown 时兼容 Windows 换行，原始备份字节不变。
        text = text.replace("\r\n", "\n")

        if not text.startswith("---\n"):
            raise ValueError("记忆文件缺少 YAML 元数据")

        parts = text[4:].split("\n---\n", 1)

        if len(parts) != 2:
            raise ValueError("记忆元数据没有正确结束")

        metadata_text, content = parts

        metadata = MemoryMetadata.model_validate(
            yaml.safe_load(metadata_text)
        )

        return MemoryDocument(
            filename=filename,
            metadata=metadata,
            content=content,
        )

    def list_documents(self) -> list[MemoryDocument]:
        return [
            self.read(path.name)
            for path in sorted(self.root.glob("*.md"))
            if path.name != "MEMORY.md"
        ]

    def _index_path(self) -> Path:
        target = self.root / "MEMORY.md"

        if (
                target.is_symlink()
                or not target.resolve().is_relative_to(self.root)
        ):
            raise ValueError("记忆索引路径不合法")

        return target

    def _index_state_path(self) -> Path:
        target = self.root / ".memory-index.json"
        if target.is_symlink() or not target.resolve().is_relative_to(self.root):
            raise ValueError("记忆索引状态路径不合法")
        return target

    def _fit_index(self, names: list[str], documents: dict[str, MemoryDocument]) -> list[str]:
        selected = []
        for name in dict.fromkeys(names):
            if name not in documents or len(selected) >= MAX_INDEX_ENTRIES:
                continue
            try:
                self._render_index([documents[item] for item in [*selected, name]])
            except ValueError:
                continue
            selected.append(name)
        return selected

    def _load_index_names(self, documents: dict[str, MemoryDocument]) -> list[str]:
        state_path = self._index_state_path()
        if state_path.exists():
            state = MemoryIndexState.model_validate_json(state_path.read_text(encoding="utf-8"))
            return [name for name in state.filenames if name in documents]

        # 首次迁移优先保留旧索引已列出的主题，不改变正文。
        index_path = self._index_path()
        if index_path.exists():
            names = re.findall(r"\]\(([a-z][a-z0-9_]{0,63}\.md)\)", index_path.read_text(encoding="utf-8"))
        else:
            names = sorted(documents)
        return self._fit_index(names, documents)

    def _plan_index(
        self, before: dict[str, MemoryDocument], after: dict[str, MemoryDocument],
        change: MemoryIndexChange | None,
    ) -> list[str]:
        selected = [name for name in self._load_index_names(before) if name in after]
        if change is None:
            # 兼容旧调用：仅新文件有机会自动收录；更新不会复活移出的条目。
            return self._fit_index([*selected, *sorted(after.keys() - before.keys())], after)
        change = MemoryIndexChange.model_validate(change.model_dump())
        if not set(change.add).issubset(after):
            raise ValueError("索引加入了不存在或即将删除的主题")
        if not set(change.remove).issubset(before.keys() | after.keys()):
            raise ValueError("索引移除了不存在的主题")
        selected = [name for name in selected if name not in change.remove]
        selected = list(dict.fromkeys([*selected, *change.add]))
        self._render_index([after[name] for name in selected])
        return selected

    def get_index_context(self) -> dict:
        with self.write_lock():
            documents = {item.filename: item for item in self.list_documents()}
            names = self._fit_index(self._load_index_names(documents), documents)
            text = self._render_index([documents[name] for name in names])
            return {
                "indexed_filenames": names,
                "index_entries": len(names), "max_index_entries": MAX_INDEX_ENTRIES,
                "index_bytes": len(text.encode("utf-8")), "max_index_bytes": MAX_INDEX_BYTES,
                "topic_count": len(documents),
                "topic_count_is_unlimited": True,
            }

    def apply_changes(
        self, documents: list[MemoryDocument], *, deletes: list[str] | None = None,
        index_change: MemoryIndexChange | None = None, backup: bool = False,
        proposal_json: str | None = None,
        backup_kind: Literal["dream", "extract"] = "dream",
    ) -> dict:
        """主题与索引一起预检；索引容量不限制主题文件数量。"""
        with self.write_lock():
            documents = [MemoryDocument.model_validate(item.model_dump()) for item in documents]
            deleted = set(deletes or [])
            rewritten = {item.filename for item in documents}
            if len(rewritten) != len(documents) or rewritten & deleted:
                raise ValueError("主题修改重复或与删除冲突")
            before = {item.filename: item for item in self.list_documents()}
            if not deleted.issubset(before):
                raise ValueError("删除的主题不存在")
            after = {name: item for name, item in before.items() if name not in deleted}
            after.update({item.filename: item for item in documents})
            selected = self._plan_index(before, after, index_change)
            payloads = {
                item.filename: self._serialize_document(item).encode("utf-8")
                for item in documents
            }
            payloads["MEMORY.md"] = self._render_index([after[name] for name in selected]).encode("utf-8")
            payloads[".memory-index.json"] = MemoryIndexState(filenames=selected).model_dump_json(indent=2).encode("utf-8")
            targets = {name: self._topic_path(name) for name in rewritten | deleted}
            targets["MEMORY.md"] = self._index_path()
            targets[".memory-index.json"] = self._index_state_path()
            originals = {name: path.read_bytes() if path.exists() else None for name, path in targets.items()}
            backup_path = None
            with TemporaryDirectory(prefix=".memory-stage-", dir=self.root) as directory:
                staged = Path(directory)
                for name, content in payloads.items():
                    (staged / name).write_bytes(content)
                if backup:
                    if backup_kind not in {"dream", "extract"}:
                        raise ValueError("记忆备份类型不合法")
                    backup_root = self.root / f".{backup_kind}-backups"
                    if backup_root.is_symlink():
                        raise ValueError("备份目录不能是符号链接")
                    backup_root.mkdir(exist_ok=True)
                    backup_path = backup_root / uuid4().hex
                    backup_path.mkdir()
                    backup_manifest = {
                        "kind": backup_kind,
                        "status": "prepared",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "file_sha256": {
                            name: hashlib.sha256(content).hexdigest()
                            for name, content in originals.items() if content is not None
                        },
                    }
                    # 先标记未完成；中途失败的目录不会被新版本工具列出。
                    (backup_path / "backup.json").write_text(
                        json.dumps(backup_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    for name, content in originals.items():
                        if content is not None:
                            (backup_path / name).write_bytes(content)
                    if proposal_json is not None:
                        (backup_path / "proposal.json").write_text(proposal_json, encoding="utf-8")
                touched = []
                try:
                    for name in sorted(rewritten):
                        touched.append(name)
                        os.replace(staged / name, targets[name])
                    for name in sorted(deleted):
                        touched.append(name)
                        targets[name].unlink()
                    for name in ["MEMORY.md", ".memory-index.json"]:
                        touched.append(name)
                        os.replace(staged / name, targets[name])
                    if backup_path is not None:
                        backup_manifest["status"] = "published"
                        marker = backup_path / ".backup.tmp"
                        marker.write_text(json.dumps(backup_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                        os.replace(marker, backup_path / "backup.json")
                except OSError:
                    for name in reversed(touched):
                        if originals[name] is None:
                            targets[name].unlink(missing_ok=True)
                        else:
                            targets[name].write_bytes(originals[name])
                    raise
            return {
                "indexed_filenames": selected,
                "unindexed_filenames": sorted(rewritten - set(selected)),
                "backup_path": str(backup_path) if backup_path is not None else None,
            }

    def _render_index(
            self,
            documents: list[MemoryDocument],
    ) -> str:
        lines = []

        for document in sorted(
                documents,
                key=lambda item: item.filename,
        ):
            # 每条记忆占一行，避免换行破坏索引格式。
            name = " ".join(document.metadata.name.split())
            description = " ".join(
                document.metadata.description.split()
            )

            # 转义 Markdown 链接标题中的特殊字符。
            name = (
                name.replace("\\", "\\\\")
                .replace("[", "\\[")
                .replace("]", "\\]")
            )

            lines.append(
                f"- [{name}]({document.filename})："
                f"{description}"
            )

        text = "\n".join(lines)
        if lines:
            text += "\n"

        if (
                len(lines) > MAX_INDEX_ENTRIES
                or len(text.encode("utf-8")) > MAX_INDEX_BYTES
        ):
            raise ValueError(
                "记忆索引超过 200 行或 25 KiB，请调整索引选择"
            )

        return text

    def _rebuild_index_unlocked(self) -> str:
        self.apply_changes([])
        return self._index_path().read_text(encoding="utf-8")

    def rebuild_index(self) -> str:
        with self.write_lock():
            return self._rebuild_index_unlocked()

    def read_index(self) -> str:
        # 只重建已收录主题的描述，不能把已移出的主题加回来。
        return self.rebuild_index()


    def _delete_unlocked(self, filename: str) -> None:
        target = self._topic_path(filename)

        if not target.is_file():
            raise FileNotFoundError(filename)

        self.apply_changes([], deletes=[filename])

    def delete(self, filename: str) -> None:
        with self.write_lock():
            self._delete_unlocked(filename)

    def list_filenames(self) -> list[str]:
        filenames = []

        for path in sorted(self.root.glob("*.md")):
            # 只处理符合主题命名规则的文件。
            # MEMORY.md 不符合小写规则，因此不会进入结果。
            if re.fullmatch(
                r"[a-z][a-z0-9_]{0,63}\.md",
                path.name,
            ) is None:
                continue

            target = self._topic_path(path.name)

            if target.is_file():
                filenames.append(path.name)

        return filenames

    def scan_headers(self) -> tuple[list[dict], list[dict]]:
        """最多检查每个主题的前 30 行；不解析或读取正文。"""
        candidates, skipped = [], []
        for filename in self.list_filenames():
            target = self._topic_path(filename)
            try:
                with target.open("rb") as file:
                    if file.readline(4097).decode("utf-8").strip() != "---":
                        raise ValueError("缺少 frontmatter")
                    header = []
                    for _ in range(29):
                        raw = file.readline(4097)
                        if not raw or len(raw) > 4096:
                            raise ValueError("frontmatter 不完整或单行过长")
                        line = raw.decode("utf-8")
                        if line.strip() == "---":
                            break
                        header.append(line)
                    else:
                        raise ValueError("前 30 行内没有完整 frontmatter")
                metadata = MemoryMetadata.model_validate(
                    yaml.safe_load("".join(header))
                )
                candidates.append({
                    "filename": filename,
                    "path": str(target),
                    "modified_at": datetime.fromtimestamp(
                        target.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "description": metadata.description,
                    "type": metadata.type,
                })
            except (OSError, ValueError, yaml.YAMLError) as exc:
                skipped.append({"filename": filename, "reason": str(exc)})
        return candidates, skipped

    def read_text(self, filename: str) -> str:
        target = self._topic_path(filename)

        if target.stat().st_size > 64 * 1024:
            raise ValueError("记忆文件不能超过 64 KiB")

        return target.read_text(encoding="utf-8")

    @contextmanager
    def write_lock(self, *, timeout: float = 0):
        with self._write_lock.acquire(timeout=timeout):
            yield
