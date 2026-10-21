from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.dream_models import (
    DreamConflict,
    DreamProposal,
)
from forgepilot.memory.dream_validation import (
    document_sha256,
    validate_dream_proposal,
)
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.store import MarkdownMemoryStore


def expect_rejected(action, expected_message: str):
    try:
        action()
    except ValueError as exc:
        assert expected_message in str(exc), str(exc)
    else:
        raise AssertionError("这个候选应该被拒绝")


def main():
    with TemporaryDirectory() as directory:
        store = MarkdownMemoryStore(Path(directory))

        for filename, content in [
            ("testing_preferences.md", "优先使用 unittest。"),
            ("verification_preferences.md", "按实际结果报告测试。"),
        ]:
            store.save(MemoryDocument(
                filename=filename,
                metadata=MemoryMetadata(
                    name="测试偏好",
                    type="feedback",
                    description=content,
                    source_session_id="old-session",
                    source_entry_ids=["old-entry"],
                ),
                content=content,
            ))

        # 模拟生成候选前，读取到的文件版本。
        versions = {
            filename: document_sha256(store.read(filename))
            for filename in store.list_filenames()
        }

        proposal = DreamProposal.model_validate({
            "rewrites": [{
                "filename": "testing_preferences.md",
                "name": "测试偏好",
                "type": "feedback",
                "description": "测试框架与报告要求。",
                "content": "优先使用 unittest，按实际结果报告测试。",
                "source_files": [
                    "testing_preferences.md",
                    "verification_preferences.md",
                ],
            }],
            "deletes": [{
                "filename": "verification_preferences.md",
                "reason": "内容已合并到测试偏好主题。",
                "replacement": "testing_preferences.md",
                "source_files": [
                    "testing_preferences.md",
                    "verification_preferences.md",
                ],
            }],
            "conflicts": [],
        })

        def validate(candidate, read_versions=None):
            return validate_dream_proposal(
                candidate,
                memory_store=store,
                read_versions=(
                    versions
                    if read_versions is None
                    else read_versions
                ),
                fully_read=set(versions),
            )

        before = {
            path.name: path.read_bytes()
            for path in store.root.glob("*.md")
        }

        assert validate(proposal) == proposal

        after = {
            path.name: path.read_bytes()
            for path in store.root.glob("*.md")
        }

        assert before == after
        print("合法合并通过，校验不修改文件：通过")

        incomplete_versions = {
            "testing_preferences.md": versions[
                "testing_preferences.md"
            ]
        }

        expect_rejected(
            lambda: validate(proposal, incomplete_versions),
            "必须先读取来源",
        )
        print("没有读取全部来源时拒绝：通过")

        bad = proposal.model_copy(deep=True)
        bad.deletes[0].replacement = None

        expect_rejected(
            lambda: validate(bad),
            "删除必须有本次提交的合并目标",
        )
        print("删除没有合并目标时拒绝：通过")

        bad = proposal.model_copy(deep=True)
        bad.conflicts.append(DreamConflict(
            filenames=[
                "testing_preferences.md",
                "verification_preferences.md",
            ],
            question="这些规则是否仍然有效？",
        ))

        expect_rejected(
            lambda: validate(bad),
            "冲突主题不能同时修改或删除",
        )
        print("待确认的冲突主题不能同时被修改：通过")

        # 模拟候选生成后，另一轮提取更新了来源文件。
        changed = store.read("verification_preferences.md")
        changed.content = "报告测试结果时，还需要列出运行命令。"
        store.save(changed)

        expect_rejected(
            lambda: validate(proposal),
            "来源主题已变化",
        )
        print("来源更新后拒绝旧候选：通过")


if __name__ == "__main__":
    main()