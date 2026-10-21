"""只读当前记忆范围中的旧版 Markdown，不访问原始会话文件。"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from forgepilot.memory.store import MarkdownMemoryStore


class MarkdownMemoryVersions:
    def __init__(self, memory_store: MarkdownMemoryStore):
        self.memory_store = memory_store

    def _topic_name(self, filename: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}\.md", filename) is None:
            raise ValueError("旧记忆主题文件名不合法")

    def _backup_root(self, kind: str) -> Path:
        if kind not in {"extract", "dream"}:
            raise ValueError("记忆版本类型不合法")
        root = self.memory_store.root / f".{kind}-backups"
        if root.is_symlink() or not root.resolve().is_relative_to(self.memory_store.root):
            raise ValueError("记忆版本目录不在当前范围内")
        return root

    def _directory(self, version_id: str) -> tuple[str, Path]:
        match = re.fullmatch(r"(extract|dream)/([0-9a-f]{32})", version_id)
        if match is None:
            raise ValueError("记忆版本 ID 格式不合法")
        kind, identifier = match.groups()
        root = self._backup_root(kind)
        directory = root / identifier
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("记忆版本目录不合法")
        if not directory.is_dir():
            raise ValueError("记忆版本不存在")
        return kind, directory

    def _checked_file(self, directory: Path, name: str) -> Path:
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("旧记忆文件路径不合法")
        return path

    def _load(self, filename: str, version_id: str) -> tuple[str, dict]:
        self._topic_name(filename)
        kind, directory = self._directory(version_id)
        marker = self._checked_file(directory, "backup.json")
        manifest = None
        if marker.exists():
            if marker.stat().st_size > 64 * 1024:
                raise ValueError("备份清单过大")
            manifest = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("kind") != kind:
                raise ValueError("备份清单不合法")
            if manifest.get("status") != "published":
                raise ValueError("记忆备份尚未完成发布")
            if not isinstance(manifest.get("file_sha256"), dict):
                raise ValueError("备份清单缺少文件校验信息")

        path = self._checked_file(directory, filename)
        if not path.is_file():
            raise ValueError("该版本没有这个主题的旧正文")
        if path.stat().st_size > 64 * 1024:
            raise ValueError("旧记忆文件超过 64 KiB")
        raw = path.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
        if manifest is not None and manifest["file_sha256"].get(filename) != sha256:
            raise ValueError("旧记忆文件与备份校验信息不一致")
        text = raw.decode("utf-8")
        document = self.memory_store.parse_document(filename, text)
        created_at = (
            manifest.get("created_at") if manifest is not None
            else datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc).isoformat()
        )
        if not isinstance(created_at, str):
            raise ValueError("备份时间不合法")
        return text, {
            "filename": filename, "version_id": version_id,
            "kind": kind, "created_at": created_at,
            "backup_status": "published" if manifest is not None else "legacy_unknown",
            "metadata": document.metadata.model_dump(),
            "sha256": sha256,
            "related_filenames": sorted(
                name for name in (manifest["file_sha256"] if manifest is not None else {filename: sha256})
                if re.fullmatch(r"[a-z][a-z0-9_]{0,63}\.md", name)
            ),
        }

    def list_versions(self, filename: str, *, offset: int = 0, limit: int = 20) -> dict:
        self._topic_name(filename)
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError("版本列表分页参数不合法")
        with self.memory_store.write_lock():
            versions, skipped = [], 0
            for kind in ["extract", "dream"]:
                root = self._backup_root(kind)
                if not root.exists():
                    continue
                for directory in root.iterdir():
                    if re.fullmatch(r"[0-9a-f]{32}", directory.name) is None:
                        continue
                    version_id = f"{kind}/{directory.name}"
                    # 没有保存这个主题的备份，不计入其历史版本。
                    if not (directory / filename).exists():
                        continue
                    try:
                        _, info = self._load(filename, version_id)
                    except (ValueError, OSError):
                        skipped += 1
                        continue
                    versions.append({
                        key: info[key] for key in [
                            "version_id", "kind", "created_at", "backup_status", "sha256", "related_filenames",
                        ]
                    } | {
                        "name": info["metadata"]["name"],
                        "description": info["metadata"]["description"],
                        "type": info["metadata"]["type"],
                    })
            versions.sort(key=lambda item: (item["created_at"], item["version_id"]), reverse=True)
            if offset > len(versions):
                raise ValueError("offset 超出版本列表长度")
            end = min(offset + limit, len(versions))
            return {
                "filename": filename, "versions": versions[offset:end],
                "total_matches": len(versions), "skipped": skipped,
                "next_offset": end if end < len(versions) else None,
            }

    def read_version(self, filename: str, version_id: str, *, offset: int = 0, limit: int = 2000, consume_read=None) -> dict:
        if offset < 0 or not 1 <= limit <= 8000:
            raise ValueError("旧记忆读取参数不合法")
        with self.memory_store.write_lock():
            text, info = self._load(filename, version_id)
            if offset > len(text):
                raise ValueError("offset 超出旧记忆长度")
            end = min(offset + limit, len(text))
            chunk = text[offset:end]
            if consume_read is not None and chunk:
                chunk = consume_read(chunk)
                end = offset + len(chunk)
            return {
                **info, "format": "markdown", "historical": True,
                "offset": offset, "end": end, "total_chars": len(text),
                "next_offset": end if end < len(text) else None,
                "content": chunk,
            }
