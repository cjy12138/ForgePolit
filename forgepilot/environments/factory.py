from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.environments.base import Environment
from forgepilot.environments.local import LocalEnvironment


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    environment_class: str = Field(
        default="local",
        min_length=1,
    )

_ENVIRONMENT_FACTORIES: dict[
    str, Callable[[], Environment]
] = {
    "local": LocalEnvironment,
}

def get_environment(config: dict) -> Environment:
    validated = EnvironmentConfig.model_validate(config)

    factory  = _ENVIRONMENT_FACTORIES.get(validated.environment_class)

    if factory is None:
        available = ", ".join(sorted(_ENVIRONMENT_FACTORIES))
        raise ValueError(
            f"未知执行环境：{validated.environment_class}；"
            f"当前支持：{available}"
        )

    environment = factory()

    if not callable(getattr(environment, "execute", None)):
        raise TypeError("执行环境必须提供可调用的 execute 方法")

    return environment