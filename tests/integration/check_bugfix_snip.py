"""用脚本模型验证 bugfix 长输出、Snip 和原文读回，不请求 API。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.bugfix.check_bugfix_workspace import git
from forgepilot.artifacts.store import ArtifactRef, ArtifactStore
from forgepilot.bugfix_service import BugfixService
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.budget.token_estimator import estimate_request_tokens
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore


class ScriptedModel:
    max_output_tokens = 2048

    def __init__(self):
        self.calls = 0
        self.saw_snip = False

    def estimate_input_tokens(self, *, messages, tools):
        return estimate_request_tokens({"messages": messages, "tools": tools})

    def generate(self, *, messages, tools):
        assert "read_artifact" in {tool["name"] for tool in tools}
        self.saw_snip |= any(message.get("snipped") for message in messages)
        self.calls += 1
        if self.calls <= 6:
            return {
                "type": "tool_call",
                "id": f"call_{self.calls}",
                "name": "run_command",
                "arguments": {"command": "python -c \"print('x'*20000)\""},
            }
        if self.calls == 7:
            first = next(message for message in messages if message.get("role") == "tool")
            assert first.get("snipped") is True
            return {
                "type": "tool_call",
                "id": "read_original",
                "name": "read_artifact",
                "arguments": {"artifact_id": first["artifact"]["id"], "offset": 4000, "limit": 100},
            }
        assert self.calls == 8
        restored = json.loads(messages[-1]["content"])
        assert restored["content"] == "x" * 100
        return {"type": "final", "content": "已验证长输出可读回；未修改代码。"}


def main():
    with TemporaryDirectory(prefix="forgepilot-bugfix-snip-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        (source / "README.md").write_text("demo\n", encoding="utf-8")
        git(source, "init")
        git(source, "add", ".")
        git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
        model = ScriptedModel()
        result = BugfixService(
            model=model,
            source=source,
            tasks_root=root / "tasks",
            budget=ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=2000),
        ).run("验证长输出读回，不修改文件。")
        assert result["status"] == "completed", result
        assert model.saw_snip
        run_dir = Path(result["run_dir"])
        store = SessionStore(run_dir / "session.jsonl")
        assert store.get_current_snip_records()
        original = next(
            entry.payload for entry in store.get_current_branch()
            if isinstance(entry, MessageEntry) and entry.payload.get("role") == "tool"
        )
        assert not original.get("snipped")
        assert "x" * 20000 in original["content"]
        artifacts = ArtifactStore(run_dir / "artifacts")
        assert artifacts.read_text(ArtifactRef.model_validate(original["artifact"])) == original["content"]
        assert (source / "README.md").read_text(encoding="utf-8") == "demo\n"
        git(source, "diff", "--exit-code", "HEAD", "--")
        print("bugfix 自动 Snip、原文存档及分段读回：通过")


if __name__ == "__main__":
    main()
