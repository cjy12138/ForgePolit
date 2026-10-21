import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.memory.dream_agent import AutoDreamAgent
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.memory.dream_validation import (
    validate_dream_proposal,
)

class FakeDreamModel:
    max_output_tokens = 1024

    def __init__(self):
        self.calls = 0
        self.estimated_tokens = 100
        self.read_sha256 = None

    def estimate_input_tokens(self, *, messages, tools):
        return self.estimated_tokens

    def generate(self, *, messages, tools):
        self.calls += 1

        if self.calls == 1:
            data = json.loads(messages[1]["content"])
            assert "testing_preferences.md" in data["manifest"]

            assert {tool["name"] for tool in tools} == {
                "glob_memory",
                "grep_memory",
                "read_memory",
                "list_memory_versions",
                "read_memory_version",
                "submit_dream_proposal",
            }

            return {
                "type": "tool_call",
                "id": "read_1",
                "name": "read_memory",
                "arguments": {
                    "filename": "testing_preferences.md",
                },
            }

        if self.calls == 2:
            observation = messages[-1]

            assert observation["role"] == "tool"
            assert observation["tool_call_id"] == "read_1"
            assert observation["is_error"] is False

            document = json.loads(observation["content"])
            assert document["content"] == (
                "根据实际结果报告测试情况，不虚构测试结果。"
            )
            assert document["next_offset"] is None

            self.read_sha256 = document["document_sha256"]

            return {
                "type": "tool_call",
                "id": "submit_1",
                "name": "submit_dream_proposal",
                "arguments": {
                    "rewrites": [{
                        "filename": "testing_preferences.md",
                        "name": "测试偏好",
                        "type": "feedback",
                        "description": "按实际执行结果报告测试。",
                        "content": "按实际执行结果报告测试，不虚构结果。",
                        "source_files": ["testing_preferences.md"],
                    }],
                    "deletes": [],
                    "conflicts": [],
                },
            }

        raise AssertionError("提交后不应继续请求模型")


def main():
    with TemporaryDirectory() as directory:
        store = MarkdownMemoryStore(Path(directory))

        store.save(MemoryDocument(
            filename="testing_preferences.md",
            metadata=MemoryMetadata(
                name="测试偏好",
                type="feedback",
                description="测试报告必须有实际依据。",
                source_session_id="old-session",
                source_entry_ids=["old-entry"],
            ),
            content="根据实际结果报告测试情况，不虚构测试结果。",
        ))

        before = {
            path.name: path.read_bytes()
            for path in store.root.glob("*.md")
        }

        budget = ContextBudget(
            context_window=16000,
            max_output_tokens=1024,
            safety_margin=1000,
        )

        model = FakeDreamModel()
        agent = AutoDreamAgent(
            model=model,
            memory_store=store,
            context_budget=budget,
        )

        proposal = agent.prepare()

        assert model.calls == 2
        assert len(proposal.rewrites) == 1
        assert proposal.rewrites[0].source_files == [
            "testing_preferences.md"
        ]
        print("读取记忆、接收工具结果、提交整理候选：通过")

        assert agent.read_versions == {
            "testing_preferences.md": model.read_sha256
        }
        print("记录实际读取的文件版本：通过")

        after = {
            path.name: path.read_bytes()
            for path in store.root.glob("*.md")
        }
        assert before == after
        print("准备候选不修改主题和索引：通过")

        # 请求超过预算，必须在调用模型之前停止。
        oversized_model = FakeDreamModel()
        oversized_model.estimated_tokens = 100000

        oversized_agent = AutoDreamAgent(
            model=oversized_model,
            memory_store=store,
            context_budget=budget,
        )

        try:
            oversized_agent.prepare()
        except ModelError as exc:
            assert exc.code == "autodream_budget_exceeded"
        else:
            raise AssertionError("超过预算应拒绝请求")

        assert oversized_model.calls == 0
        print("超过预算不调用模型：通过")

        # 创建独立对象，避免沿用前面已完整读取的状态。
        reader = AutoDreamAgent(
            model=FakeDreamModel(),
            memory_store=store,
            context_budget=budget,
        )

        filename = "testing_preferences.md"

        def read_chunk(offset: int, limit: int) -> dict:
            result = reader.registry.execute(
                "read_memory",
                {
                    "filename": filename,
                    "offset": offset,
                    "limit": limit,
                },
                context=reader.context,
            )

            assert result.ok, result.output
            document = json.loads(result.output)
            reader._record_memory_read(document)
            return document

        # 只读前5个字符，不能提交涉及该文件的整理候选。
        read_chunk(0, 5)

        assert reader.read_progress[filename] == 5
        assert filename not in reader.fully_read

        try:
            validate_dream_proposal(
                proposal,
                memory_store=store,
                read_versions=reader.read_versions,
                fully_read=reader.fully_read,
            )
        except ValueError as exc:
            assert "必须完整读取来源正文" in str(exc)
        else:
            raise AssertionError("只读部分正文应拒绝候选")

        print("只读取部分正文不能通过候选校验：通过")

        # 跳到第10个字符读取尾部，中间[5, 10)没有读取。
        read_chunk(10, 8000)

        assert reader.read_progress[filename] == 5
        assert filename not in reader.fully_read
        print("跳过中间片段不能算完整读取：通过")

        # 按连续位置续读，覆盖剩余正文。
        last_chunk = read_chunk(5, 8000)

        assert reader.read_progress[filename] == last_chunk["total_chars"]
        assert filename in reader.fully_read

        assert validate_dream_proposal(
            proposal,
            memory_store=store,
            read_versions=reader.read_versions,
            fully_read=reader.fully_read,
        ) == proposal

        print("从连续位置续读完成后，候选校验通过：通过")

        # 同一次整理中，不能把两个不同版本的片段拼在一起。
        changed = store.read(filename)
        changed.content += "\n报告时注明测试命令。"
        store.save(changed)

        try:
            read_chunk(0, 5)
        except ValueError as exc:
            assert "读取期间主题已变化" in str(exc)
        else:
            raise AssertionError("读取版本变化时应停止整理")

        print("读取期间版本变化时拒绝继续：通过")


if __name__ == "__main__":
    main()
