import hashlib
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

class ArtifactRef(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )

    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_id: str) -> Path:
        path = (self.root / f"{artifact_id}.txt").resolve()

        if path.parent != self.root:
            raise ValueError("产物路径超出存储目录")

        return path

    def save_text(self, text: str) -> ArtifactRef:
        if not isinstance(text, str):
            raise TypeError("产物内容必须是字符串")

        data = text.encode("utf-8")

        ref = ArtifactRef(
            id=uuid4().hex,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

        path = self._path(ref.id)

        # x 表示只创建新文件，已有文件不能被覆盖。
        with path.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())

        # 只有写入成功后才返回引用。
        return ref

    def read_text(self, ref: ArtifactRef) -> str:
        path = self._path(ref.id)
        data = path.read_bytes()

        if len(data) != ref.size_bytes:
            raise ValueError("产物大小校验失败")

        actual_hash = hashlib.sha256(data).hexdigest()

        if actual_hash != ref.sha256:
            raise ValueError("产物内容哈希校验失败")

        return data.decode("utf-8")