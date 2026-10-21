from tempfile import TemporaryDirectory
from pathlib import Path

from forgepilot.sessions.collapse.planning import (
    collapse_source_digest,
    plan_collapse,
)
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory() as directory:
        store = SessionStore(Path(directory) / "session.jsonl")
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        ))

        user = store.append_message({
            "role": "user",
            "content": "检查项目，不修改测试文件。",
        })

        calls = []
        results = []

        for index in range(4):
            call_id = f"call_{index}"

            calls.append(store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments": {"path": "README.md"},
            }))

            results.append(store.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"第 {index} 组输出",
                "is_error": False,
                "exit_code": 0,
            }))

        branch = store.get_current_branch()
        before = store.path.read_bytes()

        # 只选择第二组，第一组和最后两组不进入摘要范围。
        plan = plan_collapse(
            branch,
            source_start_id=calls[1].id,
            source_end_id=results[1].id,
        )

        assert [
            node.id
            for node in plan.to_summarize
        ] == [calls[1].id, results[1].id]

        assert plan.source_leaf_id == results[-1].id
        assert plan.source_sha256 == collapse_source_digest(
            plan.to_summarize
        )
        assert store.path.read_bytes() == before
        print("选择中间完整交互组，不修改历史：通过")

        original_hash = plan.source_sha256
        plan.to_summarize[1].payload["content"] = "修改副本"

        assert results[1].payload["content"] == "第 1 组输出"
        assert collapse_source_digest(plan.to_summarize) != original_hash
        print("计划使用副本，来源变化可检测：通过")

        invalid_ranges = [
            # 只选择结果，切开工具交互。
            (results[1].id, results[1].id),
            # 最近两组属于保护区。
            (calls[3].id, results[3].id),
            # 包含原始用户要求。
            (user.id, results[1].id),
        ]

        for start_id, end_id in invalid_ranges:
            try:
                plan_collapse(
                    branch,
                    source_start_id=start_id,
                    source_end_id=end_id,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("不允许的折叠范围被接受")

        print("保护调用配对、近期交互和原始要求：通过")


if __name__ == "__main__":
    main()