from dataclasses import dataclass

from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.summarization.artifacts import SummaryArtifactSource


@dataclass(frozen=True)
class SummarizationResult:
    summary: ContextSummary
    artifact_sources: list[SummaryArtifactSource]

