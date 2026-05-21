"""Cold-start config: logger, thresholds loader, detector factory.

Keeps all environment-driven wiring out of ``lambda_handler`` so the handler
is a thin orchestrator and everything here is straightforward to mock in
unit tests.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from detector import DEFAULT_THRESHOLDS, BaseDetector, ThresholdDetector


def configure_logging() -> logging.Logger:
    """Configure the package root logger from ``LOG_LEVEL`` env var."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, force=True)
    return logging.getLogger("anomaly_detector")


def build_s3_client():
    """S3 client following the project-wide env-driven pattern.

    Mirrors ``loaders._s3_client`` (``src/scripts/loaders.py``): honours
    ``AWS_ENDPOINT_URL`` for LocalStack, falls back to IAM-role creds on AWS.
    """
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def build_sns_client():
    """SNS client following the project-wide env-driven pattern."""
    return boto3.client(
        "sns",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def load_thresholds(s3_client=None, logger: logging.Logger | None = None) -> dict[str, float]:
    """Load anomaly thresholds from S3, falling back to ``DEFAULT_THRESHOLDS``.

    Mirrors the cold-start "load-or-default" pattern used for the MinMax
    scaler in ``bronze_to_silver_etl.py``. The artifact is written by
    ``silver_to_gold_etl.py`` ``_publish_thresholds`` after a successful
    Gold materialisation.

    Returns: dict with keys ``oil_temp_max``, ``oil_press_min``,
    ``cht_spread_max``, ``egt_spread_max`` — always populated.
    """
    log = logger or logging.getLogger("anomaly_detector")
    bucket = os.environ.get("THRESHOLDS_BUCKET")
    key = os.environ.get("THRESHOLDS_KEY")
    if not bucket or not key:
        log.info("THRESHOLDS_BUCKET/KEY unset — using DEFAULT_THRESHOLDS")
        return dict(DEFAULT_THRESHOLDS)

    s3 = s3_client or build_s3_client()
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        log.info(
            "Thresholds artifact unavailable at s3://%s/%s (%s) — using defaults",
            bucket, key, exc.__class__.__name__,
        )
        return dict(DEFAULT_THRESHOLDS)

    try:
        doc: dict[str, Any] = json.loads(body)
        # Accept both {"thresholds": {...}} (the published shape) and a flat dict.
        thresholds = doc.get("thresholds", doc) if isinstance(doc, dict) else {}
        merged = dict(DEFAULT_THRESHOLDS)
        for k in DEFAULT_THRESHOLDS:
            if k in thresholds:
                merged[k] = float(thresholds[k])
        log.info("Loaded thresholds from s3://%s/%s: %s", bucket, key, merged)
        return merged
    except (ValueError, TypeError) as exc:
        log.info("Malformed thresholds JSON (%s) — using defaults", exc.__class__.__name__)
        return dict(DEFAULT_THRESHOLDS)


def build_detector(thresholds: dict[str, float] | None = None) -> BaseDetector:
    """Factory selecting the detector implementation.

    ``DETECTOR_TYPE`` env var picks the implementation; defaults to
    ``threshold``. The ``model`` branch is a documented seam — a future PR
    will load an ONNX/sklearn artifact and return a ``ModelDetector``.
    """
    kind = os.environ.get("DETECTOR_TYPE", "threshold").lower()
    if kind == "threshold":
        return ThresholdDetector(thresholds)
    raise NotImplementedError(
        f"Detector type {kind!r} not implemented yet — only 'threshold' ships in this PR."
    )
