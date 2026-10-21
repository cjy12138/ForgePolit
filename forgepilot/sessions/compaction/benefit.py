from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.context import build_request_messages
from forgepilot.sessions.models import CompactionEntry, SessionNode
from forgepilot.sessions.snip.request_view import build_snipped_request

class CompactionBenefitCheck:
    def __init__(
        self,
        *,
        model,
        tools,
        max_input_tokens: int,
        minimum_savings_tokens: int = 256,
        artifacts: ArtifactStore | None = None,
    ):
        for name, value in (
            ("max_input_tokens", max_input_tokens),
            ("minimum_savings_tokens", minimum_savings_tokens),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} 必须是正整数")

        self.model = model
        self.tools = tools
        self.max_input_tokens = max_input_tokens
        self.minimum_savings_tokens = minimum_savings_tokens
        self.artifacts = artifacts

    def estimate(self, branch):
        if self.artifacts is not None:
            messages = build_snipped_request(
                branch,
                self.artifacts,
                keep_recent_tool_groups=2,
            )
        else:
            messages = build_request_messages(branch)

        return self.model.estimate_input_tokens(
            messages=messages,
            tools=self.tools,
        )

    def __call__(
            self,
            branch: list[SessionNode],
            entry: CompactionEntry,
    ) -> None:
        before = self.estimate(branch)
        after = self.estimate([*branch, entry])

        if after > self.max_input_tokens:
            raise ModelError(
                "compaction_candidate_over_budget",
                (
                    f"候选摘要构造后的输入估算为 {after} Token，"
                    f"仍超过预算 {self.max_input_tokens} Token。"
                    "压缩节点未发布。"
                ),
            )

        savings = before - after

        if savings < self.minimum_savings_tokens:
            raise ModelError(
                "compaction_insufficient_savings",
                (
                    f"候选摘要预计节省 {savings} Token，"
                    f"低于最小收益 {self.minimum_savings_tokens} Token。"
                    "压缩节点未发布。"
                ),
            )



