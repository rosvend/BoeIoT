"""Pluggable anomaly detectors.

Pure-Python, no AWS dependencies — kept easy to unit test and easy to swap.
The default ``ThresholdDetector`` is the "mock" detector permitted by the spec;
a real ``ModelDetector`` (sklearn/ONNX/PyTorch) can drop in behind the same
``BaseDetector`` interface without touching the handler.

Thresholds are expressed in the [0, 1] Min-Max-normalised space that Silver
materialises in ``bronze_to_silver_etl.py``. Producing frames in that same
space keeps the comparison physically meaningful end-to-end.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# Sensible physical defaults in the [0, 1] Silver-normalised space. Used when
# the s3://...-silver-layer/artifacts/anomaly_thresholds.json artifact is not
# yet published — e.g. before the Silver→Gold ETL has run for the first time.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "oil_temp_max":   0.85,   # E1_OilT upper bound
    "oil_press_min":  0.10,   # E1_OilP lower bound
    "cht_spread_max": 0.30,   # cht_spread upper bound (cylinder imbalance)
    "egt_spread_max": 0.35,   # egt_spread upper bound (combustion imbalance)
}


class BaseDetector(ABC):
    """Interface every detector implementation must satisfy."""

    @abstractmethod
    def detect(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a list of anomaly dicts found in ``frame``.

        An empty list means "frame is healthy". Each anomaly dict carries
        ``type`` (machine-readable kind), the observed ``value``, and the
        ``threshold`` it crossed.
        """


class ThresholdDetector(BaseDetector):
    """Static-threshold detector backed by a dict of bounds.

    The four checks mirror the percentile flags already used in
    ``silver_to_gold_etl.compute_gold``:

    * ``E1_OilT`` >  ``oil_temp_max``     → ``high_oil_temp``
    * ``E1_OilP`` <  ``oil_press_min``    → ``low_oil_pressure``
    * ``cht_spread`` > ``cht_spread_max`` → ``cht_imbalance``
    * ``egt_spread`` > ``egt_spread_max`` → ``egt_imbalance``

    A missing column is treated as "no signal, no anomaly" rather than an
    error — the producer is the contract owner, and a partial frame should
    not crash the Lambda.
    """

    def __init__(self, thresholds: dict[str, float] | None = None) -> None:
        merged = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            merged.update(thresholds)
        self.thresholds = merged

    def detect(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        anomalies: list[dict[str, Any]] = []

        oil_t = _as_float(frame.get("E1_OilT"))
        if oil_t is not None and oil_t > self.thresholds["oil_temp_max"]:
            anomalies.append({
                "type": "high_oil_temp",
                "value": oil_t,
                "threshold": self.thresholds["oil_temp_max"],
            })

        oil_p = _as_float(frame.get("E1_OilP"))
        if oil_p is not None and oil_p < self.thresholds["oil_press_min"]:
            anomalies.append({
                "type": "low_oil_pressure",
                "value": oil_p,
                "threshold": self.thresholds["oil_press_min"],
            })

        cht_spread = _as_float(frame.get("cht_spread"))
        if cht_spread is not None and cht_spread > self.thresholds["cht_spread_max"]:
            anomalies.append({
                "type": "cht_imbalance",
                "value": cht_spread,
                "threshold": self.thresholds["cht_spread_max"],
            })

        egt_spread = _as_float(frame.get("egt_spread"))
        if egt_spread is not None and egt_spread > self.thresholds["egt_spread_max"]:
            anomalies.append({
                "type": "egt_imbalance",
                "value": egt_spread,
                "threshold": self.thresholds["egt_spread_max"],
            })

        return anomalies


def _as_float(v: Any) -> float | None:
    """Coerce v to float; return None if absent or non-numeric.

    Kinesis payloads ship as JSON, so values come in as int/float/None; this
    guard tolerates strings too in case a future producer emits them.
    """
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
