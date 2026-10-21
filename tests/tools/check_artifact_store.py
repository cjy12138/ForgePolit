from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from forgepilot.artifacts.store import ArtifactRef, ArtifactStore


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory) / "artifacts"
        store = ArtifactStore(root)

        text = (
            "第一行\r\n"
            + "中文工具输出\n" * 5000
            + "最后一行：必须完整保存"
        )

        ref = store.save_text(text)

        assert ref.size_bytes == len(text.encode("utf-8"))
        assert store.read_text(ref) == text
        assert ref.size_bytes > 12000
        print("长输出、中文和换行完整保存：通过")

        # 模拟重启后，从持久化的引用重新读取。
        restored_ref = ArtifactRef.model_validate_json(
            ref.model_dump_json()
        )
        reopened = ArtifactStore(root)

        assert reopened.read_text(restored_ref) == text
        print("重新创建存储对象后仍可读回：通过")

        second = store.save_text("另一个输出")

        assert second.id != ref.id
        assert store.read_text(ref) == text
        assert store.read_text(second) == "另一个输出"
        print("不同输出不会覆盖：通过")

        # 改变一个字节，保持长度不变，验证哈希检查。
        path = root / f"{ref.id}.txt"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)

        try:
            store.read_text(ref)
        except ValueError as exc:
            assert "哈希" in str(exc)
        else:
            raise AssertionError("内容变化后不应通过校验")

        print("相同长度的内容变化也能被发现：通过")

        try:
            ArtifactRef(
                id="../outside",
                sha256="0" * 64,
                size_bytes=0,
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("不能把路径当作产物 ID")

        print("拒绝非法产物 ID：通过")


if __name__ == "__main__":
    main()