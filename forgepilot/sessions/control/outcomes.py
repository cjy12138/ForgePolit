COMPACTION_STOP_CODES = frozenset({
    "summary_job_cancelled",
    "summary_job_timeout",
    "summary_job_request_limit",
    "summary_job_token_limit",
    "summary_chunk_limit",
    "summary_budget_exceeded",
    "summary_group_too_large",
    "retained_context_too_large",
    "compaction_insufficient_savings",
    "compaction_candidate_over_budget",
})


def is_compaction_stop(code: str) -> bool:
    return code in COMPACTION_STOP_CODES