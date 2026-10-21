from forgepilot.artifacts.store import ArtifactRef


def inherit_artifacts(snapshot, *, parent, child):
    """复制继承消息引用的工具原文，保持原 ID 与哈希，不写入父目录。"""
    for message in snapshot.messages():
        if message.get("role") != "tool" or not isinstance(message.get("artifact"), dict):
            continue
        ref = ArtifactRef.model_validate(message["artifact"])
        target = child.root / f"{ref.id}.txt"
        if not target.exists():
            content = parent.read_text(ref)
            try:
                with target.open("xb") as file:
                    file.write(content.encode("utf-8"))
            except FileExistsError:
                pass
        child.read_text(ref)
