import hashlib
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.sessions.summarization.schema import ContextSummary
import json

from forgepilot.sessions.summarization.artifacts import SummaryArtifactSource
from forgepilot.sessions.summarization.result import SummarizationResult

def summary_digest(summary: ContextSummary) -> str:
    data = summary.model_dump_json().encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def result_digest(result: SummarizationResult) -> str:
    data = {
        "summary": result.summary.model_dump(mode="json"),
        "artifact_sources": [
            source.model_dump(mode="json")
            for source in result.artifact_sources
        ],
    }

    encoded = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()

class SummarySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    summary: ContextSummary
    artifact_sources: list[SummaryArtifactSource]

class SummarySnapshotStore:
    def __init__(self, path: Path):
        self.path = path

    def load_result(
            self,
            expected_key: str,
    ) -> SummarizationResult | None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

        try:
            snapshot = SummarySnapshot.model_validate_json(text)
        except ValueError:
            # 缓存格式损坏，按未命中处理。
            return None

        if snapshot.cache_key != expected_key:
            return None

        result = SummarizationResult(
            summary=snapshot.summary,
            artifact_sources=snapshot.artifact_sources,
        )

        if result_digest(result) != snapshot.result_sha256:
            return None

        return SummarizationResult(
            summary=result.summary.model_copy(deep=True),
            artifact_sources=[
                source.model_copy(deep=True)
                for source in result.artifact_sources
            ],
        )

    def save_result(
            self,
            key: str,
            result: SummarizationResult,
    ) -> None:
        snapshot = SummarySnapshot(
            cache_key=key,
            result_sha256=result_digest(result),
            summary=result.summary,
            artifact_sources=result.artifact_sources,
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{uuid4().hex}.tmp"
        )

        try:
            with temporary.open(
                "x",
                encoding="utf-8",
                newline="\n",
            ) as file:
                file.write(snapshot.model_dump_json() + "\n")
                file.flush()
                os.fsync(file.fileno())

            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load(self, expected_key: str) -> ContextSummary | None:
        result = self.load_result(expected_key)

        if result is None:
            return None

        return result.summary

    def save(self, key: str, summary: ContextSummary) -> None:
        self.save_result(
            key,
            SummarizationResult(
                summary=summary,
                artifact_sources=[],
            ),
        )

