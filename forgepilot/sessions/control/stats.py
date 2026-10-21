from dataclasses import dataclass


@dataclass
class ContextStats:
    snip_applications: int = 0
    compaction_attempts: int = 0
    compaction_successes: int = 0
    compaction_failures: int = 0
    summary_requests: int = 0
    summary_cache_hits: int = 0
    compaction_stops: int = 0
    collapse_attempts: int = 0
    collapse_successes: int = 0
    collapse_skips: int = 0
    collapse_failures: int = 0