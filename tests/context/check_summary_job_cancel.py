from tempfile import TemporaryDirectory
from threading import Event

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_chunked_summarizer import MergeModel
from tests.context.check_session_compactor import make_store


class CancelModel(MergeModel):
    def __init__(self, event, cancel_at=None):
        super().__init__()
        self.event = event
        self.cancel_at = cancel_at

    def generate(self, messages, tools):
        response = super().generate(messages, tools)

        if self.calls == self.cancel_at:
            self.event.set()

        return response


def main():
    cases = [
        ("before", None, True, False, 0),
        ("first_return", 1, False, False, 1),
        ("last_return", 2, False, False, 2),
        ("publication", None, False, True, 2),
    ]

    with TemporaryDirectory() as directory:
        for name, cancel_at, preset, cancel_on_publish, calls in cases:
            store = make_store(directory, f"{name}.jsonl")
            before = store.path.read_bytes()
            event = Event()

            if preset:
                event.set()

            model = CancelModel(event, cancel_at=cancel_at)
            chunked = ChunkedSummarizer(
                ContextSummarizer(
                    model,
                    context_budget=ContextBudget(
                        context_window=450,
                        max_output_tokens=100,
                        safety_margin=100,
                    ),
                ),
                is_cancelled=event.is_set,
            )

            def publication_check(branch, entry):
                if cancel_on_publish:
                    event.set()

            compactor = SessionCompactor(
                store,
                chunked,
                is_cancelled=event.is_set,
                publication_check=publication_check,
            )

            try:
                compactor.compact(keep_recent_groups=1)
            except ModelError as exc:
                assert exc.code == "summary_job_cancelled"
            else:
                raise AssertionError("取消后不应发布摘要")

            assert model.calls == calls
            assert store.path.read_bytes() == before
            print(f"{name}：取消生效，不发布部分或已取消摘要：通过")


if __name__ == "__main__":
    main()