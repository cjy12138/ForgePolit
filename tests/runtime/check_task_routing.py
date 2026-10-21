from pydantic import ValidationError

from forgepilot.tasks import TaskRequest, TaskIntent, route_task


def main():
    intent = TaskIntent(
        goal="修复加法函数",
        suggested_mode="bugfix",
        clarification_question="",
    )

    # 用户没有指定模式，采用模型建议。
    decision = route_task(
        TaskRequest(text="修复加法函数"),
        intent,
    )

    assert decision.status == "ready"
    assert decision.mode == "bugfix"
    print("采用建议模式：通过")

    # 用户指定 coding，即使模型建议 bugfix，也尊重用户选择。
    decision = route_task(
        TaskRequest(
            text="陪我一起修复加法函数",
            mode="coding",
        ),
        intent,
    )

    assert decision.mode == "coding"
    print("用户指定模式优先：通过")

    # 指定了模式，但任务仍缺少关键信息。
    decision = route_task(
        TaskRequest(text="修复那个问题", mode="bugfix"),
        TaskIntent(
            goal="修复用户提到的问题",
            suggested_mode="bugfix",
            clarification_question="具体是哪一个错误或失败测试？",
        ),
    )

    assert decision.status == "needs_input"
    assert decision.question
    print("信息不足进入澄清：通过")

    # 请求不能自行携带工作区路径。
    try:
        TaskRequest.model_validate({
            "text": "修复加法函数",
            "workspace": "D:/",
        })
    except ValidationError:
        print("拒绝请求指定工作区：通过")
    else:
        raise AssertionError("不应该接受 workspace 字段")


if __name__ == "__main__":
    main()