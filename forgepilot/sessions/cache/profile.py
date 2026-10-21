from dataclasses import asdict

from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.summarization.summarizer import SYSTEM_PROMPT
from forgepilot.sessions.summarization.schema import ContextSummary

def build_summary_cache_profile(
    chunked: ChunkedSummarizer,
    *,
    provider_id: str,
) -> dict:
    summarizer = chunked.summarizer
    model = summarizer.model

    model_name = getattr(model, "model_name", None)

    if not isinstance(model_name, str) or not model_name:
        raise ValueError("构造缓存配置需要明确的模型名称")

    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider_id 不能为空")

    return {
        "profile_version": 1,
        "provider_id": provider_id,
        "model_name": model_name,
        "max_output_tokens": model.max_output_tokens,
        "summary_prompt": SYSTEM_PROMPT,
        "summary_schema": ContextSummary.model_json_schema(),
        "summary_budget": asdict(summarizer.context_budget),
        "max_chunks": chunked.max_chunks,
        "overflow_recovery": {
            "policy_version": 1,
            "max_retries": 3,
            "trim_fields": ["completed", "verification"],
            "trim_order": "longest_first",
        },
        "message_view": {
            "policy_version": 1,
            "snip_enabled": summarizer.artifact_store is not None,
            "head_chars": 2000,
            "tail_chars": 1000,
            "artifact_root": (
                str(summarizer.artifact_store.root)
                if summarizer.artifact_store is not None
                else None
            ),
        },
        "model_adapter_version": 1,
    }
