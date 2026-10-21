from forgepilot.models.token_calibration import TokenEstimateCalibrator
from forgepilot.models.usage import (
    ModelUsage,
    RequestTokenMeasurement,
)


def make_measurement(estimated, actual, model_name="demo"):
    usage = None

    if actual is not None:
        usage = ModelUsage(
            prompt_tokens=actual,
            completion_tokens=20,
            total_tokens=actual + 20,
        )

    return RequestTokenMeasurement(
        model_name=model_name,
        estimated_input_tokens=estimated,
        usage=usage,
    )


def main():
    calibrator = TokenEstimateCalibrator("demo")

    assert calibrator.apply(1000) == 1000
    assert calibrator.samples == 0
    print("没有样本时保留原始估算：通过")

    accepted = calibrator.observe(
        make_measurement(1000, 1200)
    )

    assert accepted is True
    assert calibrator.apply(1000) == 1320
    assert calibrator.apply(2000) == 2640
    assert calibrator.samples == 1
    print("低估样本提高后续估算：通过")

    calibrator.observe(make_measurement(1000, 800))

    assert calibrator.apply(1000) == 1320
    assert calibrator.samples == 2
    print("较低比例不会降低已有系数：通过")

    for measurement in (
        make_measurement(1000, None),
        make_measurement(0, 1200),
        make_measurement(1000, 0),
        make_measurement(1000, 5000, model_name="other"),
    ):
        assert calibrator.observe(measurement) is False

    assert calibrator.samples == 2
    assert calibrator.apply(1000) == 1320
    print("无效或其他模型的样本不参与校准：通过")

    calibrator.observe(make_measurement(1000, 1500))

    assert calibrator.apply(1000) == 1650
    assert calibrator.samples == 3
    print("更高比例继续提高估算：通过")

    independent = TokenEstimateCalibrator("demo")
    assert independent.apply(1000) == 1000
    print("不同实例的校准状态互不影响：通过")


if __name__ == "__main__":
    main()