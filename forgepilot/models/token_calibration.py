from fractions import Fraction
from math import ceil

from forgepilot.models.usage import RequestTokenMeasurement


class TokenEstimateCalibrator:
    def __init__(
        self,
        model_name: str,
        *,
        margin_percent: int = 10,
    ):
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name 不能为空")

        if type(margin_percent) is not int or margin_percent < 0:
            raise ValueError("margin_percent 必须是非负整数")

        self.model_name = model_name
        self.margin_percent = margin_percent
        self.samples = 0
        self._factor = Fraction(1, 1)

    @property
    def factor(self) -> float:
        return float(self._factor)

    def observe(self, measurement: RequestTokenMeasurement) -> bool:
        if measurement.model_name != self.model_name:
            return False

        if measurement.usage is None:
            return False

        estimated = measurement.estimated_input_tokens
        actual = measurement.usage.prompt_tokens

        if (
            type(estimated) is not int
            or estimated <= 0
            or type(actual) is not int
            or actual <= 0
        ):
            return False

        candidate = (
            Fraction(actual, estimated)
            * Fraction(100 + self.margin_percent, 100)
        )

        self._factor = max(self._factor, candidate)
        self.samples += 1
        return True

    def apply(self, raw_estimate: int) -> int:
        if type(raw_estimate) is not int or raw_estimate < 0:
            raise ValueError("原始估算必须是非负整数")

        return ceil(raw_estimate * self._factor)