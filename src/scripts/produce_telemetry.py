"""Demo producer: replay Silver telemetry rows into the Kinesis hot path.

Streams one normalised frame per record to the
``dos-boeing-737-max-telemetry-stream`` Kinesis stream. Frames come from the
real Silver layer when available (so the demo looks like production
telemetry) and fall back to a small synthetic ring otherwise.

Usage::

    # Healthy stream (no anomalies expected)
    uv run src/scripts/produce_telemetry.py --count 30 --rate 1

    # Force an oil-temp anomaly every frame
    uv run src/scripts/produce_telemetry.py --inject-anomaly oil_temp --count 5

Anomaly injection kinds (mirrors ``ThresholdDetector`` checks):
``oil_temp``, ``oil_press``, ``cht_spread``, ``egt_spread``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Reuse the project's Silver loader (cascades S3 → local cache → inline compute).
sys.path.insert(0, os.path.dirname(__file__))
try:
    from loaders import load_silver  # type: ignore
except Exception:  # pragma: no cover — script-level import guard
    load_silver = None  # noqa: N816

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STREAM_NAME = "dos-boeing-737-max-telemetry-stream"

# Frame columns the Lambda actually inspects, plus identifiers. Everything is
# in Silver's [0, 1] normalised space.
FRAME_COLS = [
    "flight_id", "seq_idx",
    "E1_RPM", "E1_OilT", "E1_OilP", "E1_FFlow",
    "cht_mean", "cht_spread", "egt_mean", "egt_spread",
    "fqty_total", "fqty_balance",
    "IAS", "AltMSL", "VSpd",
    "flight_phase",
]

# Values used by --inject-anomaly to guarantee a threshold crossing.
ANOMALY_OVERRIDES: dict[str, dict[str, float]] = {
    "oil_temp":   {"E1_OilT": 0.97},
    "oil_press":  {"E1_OilP": 0.02},
    "cht_spread": {"cht_spread": 0.65},
    "egt_spread": {"egt_spread": 0.75},
}


def _kinesis_client():
    return boto3.client(
        "kinesis",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _silver_frames(limit: int) -> list[dict[str, Any]]:
    """Return ``limit`` frames drawn from Silver. Empty list on any failure."""
    if load_silver is None:
        return []
    try:
        df, src = load_silver()
        logger.info("Silver loaded from %s (%d rows)", src, len(df))
    except Exception as exc:
        logger.info("Silver unavailable (%s) — using synthetic frames", exc.__class__.__name__)
        return []

    cols = [c for c in FRAME_COLS if c in df.columns]
    sample = df[cols].sample(n=min(limit, len(df)), random_state=42).to_dict(orient="records")
    # Cast flight_id to str (Kinesis partition keys must be strings).
    for row in sample:
        if "flight_id" in row:
            row["flight_id"] = str(row["flight_id"])
    return sample


def _synthetic_frame(idx: int) -> dict[str, Any]:
    """Plausible healthy frame in the [0, 1] space when Silver isn't available."""
    return {
        "flight_id":    f"SYN-{idx % 5:03d}",
        "seq_idx":      idx,
        "E1_RPM":       round(random.uniform(0.55, 0.75), 3),
        "E1_OilT":      round(random.uniform(0.40, 0.65), 3),
        "E1_OilP":      round(random.uniform(0.30, 0.55), 3),
        "E1_FFlow":     round(random.uniform(0.40, 0.65), 3),
        "cht_mean":     round(random.uniform(0.35, 0.55), 3),
        "cht_spread":   round(random.uniform(0.05, 0.18), 3),
        "egt_mean":     round(random.uniform(0.45, 0.60), 3),
        "egt_spread":   round(random.uniform(0.08, 0.22), 3),
        "fqty_total":   round(random.uniform(0.40, 0.80), 3),
        "fqty_balance": round(random.uniform(-0.05, 0.05), 3),
        "IAS":          round(random.uniform(0.30, 0.65), 3),
        "AltMSL":       round(random.uniform(0.20, 0.50), 3),
        "VSpd":         round(random.uniform(0.45, 0.55), 3),
        "flight_phase": random.choice(["ascent", "cruise", "descent"]),
    }


def _apply_anomaly(frame: dict[str, Any], kind: str | None) -> dict[str, Any]:
    if not kind:
        return frame
    override = ANOMALY_OVERRIDES.get(kind)
    if override is None:
        raise SystemExit(
            f"Unknown anomaly kind {kind!r}. Choose one of: {sorted(ANOMALY_OVERRIDES)}"
        )
    return {**frame, **override}


def run(count: int, rate: float, inject: str | None, stream: str) -> int:
    """Publish ``count`` frames to Kinesis at ``rate`` frames/sec.

    Returns the number of records actually accepted by Kinesis.
    """
    kinesis = _kinesis_client()
    frames = _silver_frames(count) or [_synthetic_frame(i) for i in range(count)]
    interval = 1.0 / rate if rate > 0 else 0.0
    sent = 0

    for i, base in enumerate(frames[:count]):
        frame = _apply_anomaly(base, inject)
        try:
            kinesis.put_record(
                StreamName=stream,
                PartitionKey=str(frame.get("flight_id", "unknown")),
                Data=json.dumps(frame, default=str).encode("utf-8"),
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("PutRecord failed at frame %d (%s)", i, exc.__class__.__name__)
            return sent
        sent += 1
        logger.info(
            "PutRecord %d/%d flight=%s OilT=%s OilP=%s",
            i + 1, count, frame.get("flight_id"),
            frame.get("E1_OilT"), frame.get("E1_OilP"),
        )
        if interval > 0 and i + 1 < count:
            time.sleep(interval)

    return sent


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=10, help="Number of frames to publish.")
    p.add_argument("--rate",  type=float, default=1.0, help="Frames per second.")
    p.add_argument("--inject-anomaly", dest="inject", default=None,
                   choices=sorted(ANOMALY_OVERRIDES), help="Force every frame to cross one threshold.")
    p.add_argument("--stream", default=STREAM_NAME, help="Kinesis stream name.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    sent = run(args.count, args.rate, args.inject, args.stream)
    logger.info("Done — %d/%d records accepted by Kinesis", sent, args.count)


if __name__ == "__main__":
    main()
