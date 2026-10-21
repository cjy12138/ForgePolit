from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int | None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None


def normalize_usage(raw: Any) -> ModelUsage | None:
    if raw is None:
        return None

    def get_field(name):
        if isinstance(raw, dict):
            return raw.get(name)

        return getattr(raw, name, None)

    prompt = get_field("prompt_tokens")
    completion = get_field("completion_tokens")
    total = get_field("total_tokens")

    for value in (prompt, completion):
        if type(value) is not int or value < 0:
            return None

    if total is not None:
        if type(total) is not int or total < 0:
            return None

    return ModelUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        prompt_cache_hit_tokens=(get_field("prompt_cache_hit_tokens")
                                 if type(get_field("prompt_cache_hit_tokens")) is int
                                 and 0 <= get_field("prompt_cache_hit_tokens") <= prompt else None),
        prompt_cache_miss_tokens=(get_field("prompt_cache_miss_tokens")
                                  if type(get_field("prompt_cache_miss_tokens")) is int
                                  and 0 <= get_field("prompt_cache_miss_tokens") <= prompt else None),
    )

@dataclass(frozen=True)
class RequestTokenMeasurement:
    model_name: str
    estimated_input_tokens: int
    usage: ModelUsage | None

    @property
    def input_ratio(self) -> float | None:
        if self.usage is None or self.estimated_input_tokens <= 0:
            return None

        return (
            self.usage.prompt_tokens
            / self.estimated_input_tokens
        )
