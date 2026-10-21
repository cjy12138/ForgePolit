from copy import deepcopy

from pydantic import ValidationError

from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.schema import (
    ContextSummary,
    validate_summary_sources,
)


def main():
    source = MessageEntry(
        session_id="demo",
        payload={
            "role": "user",
            "content": (
                "修复 calculator.py 的加法函数，"
                "只修改 calculator.py。"
            ),
        },
    )

    data = {
        "goal": [
            {
                "text": "修复 calculator.py 的加法函数",
                "source_entry_ids": [source.id],
            },
        ],
        "constraints": [
            {
                "text": "只修改 calculator.py",
                "source_entry_ids": [source.id],
            },
        ],
        "completed": [],
        "in_progress": [],
        "blockers": [],
        "decisions": [],
        "next_steps": [],
        "verification": [],
        "critical_context": [],
    }

    summary = ContextSummary.model_validate(data)
    validate_summary_sources(summary, [source])
    print("结构完整且引用合法的摘要通过：通过")

    missing = deepcopy(data)
    del missing["constraints"]

    try:
        ContextSummary.model_validate(missing)
    except ValidationError:
        pass
    else:
        raise AssertionError("缺少必填分类时不应通过")

    print("拒绝缺少分类的摘要：通过")

    for text, ids in [
        ("   ", [source.id]),
        ("修复加法函数", []),
    ]:
        invalid = deepcopy(data)
        invalid["goal"][0]["text"] = text
        invalid["goal"][0]["source_entry_ids"] = ids

        try:
            ContextSummary.model_validate(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("空内容或空引用不应通过")

    print("拒绝空摘要项和无来源的摘要项：通过")

    outside = deepcopy(data)
    outside["goal"][0]["source_entry_ids"] = ["other-node"]

    summary = ContextSummary.model_validate(outside)

    try:
        validate_summary_sources(summary, [source])
    except ValueError as exc:
        assert "摘要范围外" in str(exc)
    else:
        raise AssertionError("引用范围外节点时不应通过")

    print("拒绝引用摘要范围外的节点：通过")


if __name__ == "__main__":
    main()