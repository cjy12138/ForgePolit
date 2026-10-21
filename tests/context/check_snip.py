import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.snip.transform import snip_tool_message


def main():
    with TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))

        content = (
            "输出开头\n"
            + "中间诊断信息\n" * 5000
            + "FAILED: 最后一个测试失败"
        )
        ref = artifacts.save_text(content)

        message = {
            "role": "tool",
            "tool_call_id": "call_test",
            "content": content,
            "is_error": False,
            "exit_code": 1,
            "artifact": ref.model_dump(),
        }
        before = deepcopy(message)

        view = snip_tool_message(
            message,
            artifacts,
            head_chars=100,
            tail_chars=100,
        )

        assert message == before
        assert view["snipped"] is True
        assert len(view["content"]) < len(content)
        assert "输出开头" in view["content"]
        assert "最后一个测试失败" in view["content"]
        assert artifacts.read_text(ref) == content

        for key in ("role", "tool_call_id", "is_error", "exit_code"):
            assert view[key] == message[key]

        print("只缩减副本正文，首尾、原文和结果字段保留：通过")

        repeated = snip_tool_message(
            view,
            artifacts,
            head_chars=100,
            tail_chars=100,
        )
        assert repeated == view
        print("重复缩减不叠加标记：通过")

        no_artifact = deepcopy(message)
        del no_artifact["artifact"]
        assert snip_tool_message(no_artifact, artifacts) == no_artifact
        print("没有产物引用时保留原文：通过")

        wrong = deepcopy(message)
        wrong["content"] += "不属于已保存产物的文字"

        try:
            snip_tool_message(wrong, artifacts)
        except ValueError as exc:
            assert "不一致" in str(exc)
        else:
            raise AssertionError("不能缩减来源不一致的正文")

        print("拒绝产物与正文不一致：通过")

        model = DeepSeekModel(client=None)
        request = model.prepare_request([view], [])
        api_message = request["messages"][0]
        body = json.loads(api_message["content"])

        assert api_message["tool_call_id"] == "call_test"
        assert body["ok"] is True
        assert body["exit_code"] == 1
        assert body["output"] == view["content"]
        print("实际 API 格式保留调用 ID、状态和退出码：通过")


if __name__ == "__main__":
    main()