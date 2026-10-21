import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.schema import ContextSummary


class FakeSummaryModel:
    def __init__(self, *, fail=False, during_generate=None):
        self.fail = fail
        self.during_generate = during_generate
        self.calls = 0

    def generate(self, messages, tools):
        self.calls += 1

        if self.during_generate is not None:
            self.during_generate()

        if self.fail:
            raise ModelError(
                "fake_failure",
                "模拟摘要生成失败",
            )

        data = json.loads(messages[1]["content"])
        source_id = data["messages"][0]["id"]

        arguments = {
            name: []
            for name in ContextSummary.model_fields
        }
        arguments["goal"] = [{
            "text": "用户希望了解项目。",
            "source_entry_ids": [source_id],
        }]

        return {
            "type": "tool_call",
            "id": "summary-submit",
            "name": "submit_context_summary",
            "arguments": arguments,
        }


def make_store(directory, name):
    store = SessionStore(Path(directory) / name)
    store.create(SessionHeader(
        project_id="demo",
        owner_id="local",
        cwd=directory,
    ))

    store.append_message({
        "role": "user",
        "content": "介绍一下项目。",
    })
    store.append_message({
        "role": "assistant",
        "type": "final",
        "content": "这是一个 Python Agent 项目。",
    })
    store.append_message({
        "role": "user",
        "content": "继续介绍会话模块。",
    })

    return store


def main():
    with TemporaryDirectory() as directory:
        # 1. 成功后追加压缩节点，原始历史仍在。
        store = make_store(directory, "success.jsonl")
        original = store.path.read_bytes()
        kept = store.get_current_branch()[-1]

        model = FakeSummaryModel()
        compactor = SessionCompactor(
            store,
            ContextSummarizer(model),
        )

        entry = compactor.compact(keep_recent_groups=1)

        assert entry is not None
        assert model.calls == 1
        assert store.path.read_bytes().startswith(original)

        reopened = SessionStore(store.path)
        branch = reopened.get_current_branch()
        assert branch[-1].id == entry.id

        messages = build_messages(branch)
        assert len(messages) == 2
        assert messages[-1] == kept.payload
        print("生成、保存、重新打开及上下文投影：通过")

        # 2. 刚完成压缩，没有新消息，不重复请求摘要。
        before = store.path.read_bytes()

        assert compactor.compact(
            keep_recent_groups=1
        ) is None

        assert model.calls == 1
        assert store.path.read_bytes() == before
        print("没有新增历史时不重复压缩：通过")

        # 3. 模型失败不改变会话文件。
        failed_store = make_store(directory, "failed.jsonl")
        before = failed_store.path.read_bytes()

        failed_compactor = SessionCompactor(
            failed_store,
            ContextSummarizer(FakeSummaryModel(fail=True)),
        )

        try:
            failed_compactor.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "fake_failure"
        else:
            raise AssertionError("模型失败应向上传递")

        assert failed_store.path.read_bytes() == before
        print("摘要生成失败不写入压缩节点：通过")

        # 4. 模拟生成期间会话位置发生变化。
        stale_store = make_store(directory, "stale.jsonl")
        changed = {}

        def append_during_generate():
            node = stale_store.append_message({
                "role": "user",
                "content": "补充要求：也解释分支切换。",
            })
            changed["leaf_id"] = node.id
            changed["file"] = stale_store.path.read_bytes()

        stale_compactor = SessionCompactor(
            stale_store,
            ContextSummarizer(FakeSummaryModel(
                during_generate=append_during_generate,
            )),
        )

        try:
            stale_compactor.compact(keep_recent_groups=1)
        except ValueError as exc:
            assert "位置已变化" in str(exc)
        else:
            raise AssertionError("不能发布过时的压缩计划")

        assert stale_store.path.read_bytes() == changed["file"]
        assert (
            stale_store.get_current_branch()[-1].id
            == changed["leaf_id"]
        )
        print("拒绝过时摘要，并保留期间新增的消息：通过")


if __name__ == "__main__":
    main()