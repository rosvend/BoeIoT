"""Unit tests for the threshold-based anomaly detector.

Tests are AWS-free by design — only ``detector`` (a pure-Python module) is
exercised. The handler/config code paths that hit boto3 are covered by the
end-to-end demo in ``docs/hot_path.md``.
"""

from __future__ import annotations

from detector import DEFAULT_THRESHOLDS, ThresholdDetector


def _healthy_frame() -> dict:
    """Frame whose every channel sits comfortably inside the default bounds."""
    return {
        "flight_id":   "F-TEST",
        "seq_idx":     0,
        "E1_OilT":     0.50,
        "E1_OilP":     0.45,
        "cht_spread":  0.10,
        "egt_spread":  0.15,
    }


def test_healthy_frame_yields_no_anomalies():
    det = ThresholdDetector()
    assert det.detect(_healthy_frame()) == []


def test_high_oil_temp_is_flagged():
    det = ThresholdDetector()
    frame = _healthy_frame() | {"E1_OilT": 0.95}
    anomalies = det.detect(frame)
    assert len(anomalies) == 1
    assert anomalies[0]["type"] == "high_oil_temp"
    assert anomalies[0]["value"] == 0.95
    assert anomalies[0]["threshold"] == DEFAULT_THRESHOLDS["oil_temp_max"]


def test_low_oil_pressure_is_flagged():
    det = ThresholdDetector()
    frame = _healthy_frame() | {"E1_OilP": 0.05}
    types = [a["type"] for a in det.detect(frame)]
    assert types == ["low_oil_pressure"]


def test_cht_and_egt_imbalance_can_co_occur():
    det = ThresholdDetector()
    frame = _healthy_frame() | {"cht_spread": 0.50, "egt_spread": 0.80}
    types = [a["type"] for a in det.detect(frame)]
    assert "cht_imbalance" in types
    assert "egt_imbalance" in types
    assert len(types) == 2


def test_missing_columns_are_skipped_not_raised():
    """Producer is the schema owner — a partial frame must not crash the Lambda."""
    det = ThresholdDetector()
    # Only flight_id + one signal that is *not* anomalous.
    assert det.detect({"flight_id": "F-PARTIAL", "E1_RPM": 0.6}) == []


def test_non_numeric_values_are_skipped():
    det = ThresholdDetector()
    frame = _healthy_frame() | {"E1_OilT": "not-a-number"}
    assert det.detect(frame) == []


def test_custom_thresholds_override_defaults():
    custom = {"oil_temp_max": 0.20}  # very tight upper bound
    det = ThresholdDetector(custom)
    frame = _healthy_frame() | {"E1_OilT": 0.30}
    anomalies = det.detect(frame)
    assert len(anomalies) == 1
    assert anomalies[0]["type"] == "high_oil_temp"
    assert anomalies[0]["threshold"] == 0.20


def test_partial_overrides_keep_default_keys():
    """Overriding one threshold must leave the others at their default."""
    det = ThresholdDetector({"oil_temp_max": 0.20})
    assert det.thresholds["oil_press_min"] == DEFAULT_THRESHOLDS["oil_press_min"]
    assert det.thresholds["cht_spread_max"] == DEFAULT_THRESHOLDS["cht_spread_max"]
