"""Kinesis-triggered anomaly detection Lambda.

Flow per invocation:

    Kinesis Records ──▶ base64-decode JSON frames
                  ──▶ ``ThresholdDetector.detect(frame)``
                  ──▶ if anomalies: ``sns.publish(...)``

Cold-start state (logger, SNS client, detector) is built once at module load
so warm invocations don't pay the boto3 client cost again. All endpoints are
env-driven so the exact same artifact runs against LocalStack and real AWS.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import config

# ── COLD START ────────────────────────────────────────────────────────────────
_logger = config.configure_logging()
_sns = config.build_sns_client()
_thresholds = config.load_thresholds(logger=_logger)
_detector = config.build_detector(_thresholds)
_topic_arn = os.environ.get("SNS_TOPIC_ARN", "")

if not _topic_arn:
    _logger.warning("SNS_TOPIC_ARN env var is empty — publishes will fail.")
_logger.info("Cold start complete. Detector=%s", _detector.__class__.__name__)


# ── HANDLER ───────────────────────────────────────────────────────────────────
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process a Kinesis batch and publish one SNS message per anomalous frame.

    Returns a small summary so CloudWatch Logs (and any test harness) can
    eyeball processed/alerted counts.
    """
    records = event.get("Records") or []
    alerted = 0

    for record in records:
        frame = _decode_record(record)
        if frame is None:
            continue

        anomalies = _detector.detect(frame)
        if not anomalies:
            continue

        _publish_alert(frame, anomalies)
        alerted += 1

    _logger.info("Batch done — processed=%d, alerted=%d", len(records), alerted)
    return {"processed": len(records), "alerted": alerted}


def _decode_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort decode of a single Kinesis record to a frame dict.

    Bad records are logged and skipped, not raised — a single malformed
    record must not poison a whole batch.
    """
    try:
        raw = record["kinesis"]["data"]
    except (KeyError, TypeError):
        _logger.warning("Record missing kinesis.data key — skipping")
        return None
    try:
        decoded = base64.b64decode(raw)
        return json.loads(decoded)
    except (ValueError, TypeError) as exc:
        _logger.warning("Could not decode record (%s) — skipping", exc.__class__.__name__)
        return None


def _publish_alert(frame: dict[str, Any], anomalies: list[dict[str, Any]]) -> None:
    """Publish a single SNS alert; never raises out of the handler."""
    message = json.dumps({"frame": frame, "anomalies": anomalies}, default=str)
    subject = f"Anomaly: flight {frame.get('flight_id', 'unknown')}"
    try:
        _sns.publish(TopicArn=_topic_arn, Subject=subject[:100], Message=message)
        _logger.info(
            "Published alert flight=%s types=%s",
            frame.get("flight_id"),
            [a["type"] for a in anomalies],
        )
    except Exception as exc:  # pragma: no cover — defensive
        _logger.error(
            "SNS publish failed flight=%s types=%s (%s)",
            frame.get("flight_id"),
            [a["type"] for a in anomalies],
            exc.__class__.__name__,
        )
