import hashlib
import json
from collections import OrderedDict
from copy import deepcopy

from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.cache.snapshot import SummarySnapshotStore
from forgepilot.sessions.summarization.result import SummarizationResult

class SummaryCandidateCache:
    def __init__(
            self,
            *,
            profile: dict,
            max_entries: int = 16,
            snapshot_store: SummarySnapshotStore | None = None,
    ):
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("max_entries 必须是正整数")

        self.profile = deepcopy(profile)
        self.max_entries = max_entries
        self._items: OrderedDict[str, SummarizationResult] = OrderedDict()
        self.snapshot_store = snapshot_store

    def make_key(self, plan: CompactionPlan) -> str:
        data = {
            "cache_version": 2,
            "profile": self.profile,
            "source_leaf_id": plan.source_leaf_id,
            "to_summarize": [
                entry.model_dump(mode="json")
                for entry in plan.to_summarize
            ],
            "to_keep": [
                entry.model_dump(mode="json")
                for entry in plan.to_keep
            ],
            "previous_compaction_id": plan.previous_compaction_id,
            "previous_summary": (
                plan.previous_summary.model_dump()
                if plan.previous_summary is not None
                else None
            ),
            "previous_artifact_sources": [
                source.model_dump(mode="json")
                for source in plan.previous_artifact_sources
            ],
        }

        encoded = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()

    def get_result(
            self,
            plan: CompactionPlan,
    ) -> SummarizationResult | None:
        key = self.make_key(plan)
        result = self._items.get(key)

        if result is None and self.snapshot_store is not None:
            result = self.snapshot_store.load_result(key)

            if result is not None:
                self._items[key] = deepcopy(result)

                while len(self._items) > self.max_entries:
                    self._items.popitem(last=False)

        if result is None:
            return None

        self._items.move_to_end(key)
        return deepcopy(result)

    def put_result(
            self,
            plan: CompactionPlan,
            result: SummarizationResult,
    ) -> None:
        key = self.make_key(plan)

        if self.snapshot_store is not None:
            self.snapshot_store.save_result(key, result)

        self._items[key] = deepcopy(result)
        self._items.move_to_end(key)

        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def get(self, plan: CompactionPlan) -> ContextSummary | None:
        result = self.get_result(plan)

        if result is None:
            return None

        return result.summary

    def put(
            self,
            plan: CompactionPlan,
            summary: ContextSummary,
    ) -> None:
        self.put_result(
            plan,
            SummarizationResult(
                summary=summary,
                artifact_sources=[],
            ),
        )