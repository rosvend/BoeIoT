"""Demo consumer: poll the SQS alerts queue and pretty-print each anomaly.

The SQS queue is subscribed to the SNS anomaly topic with
``raw_message_delivery = true``, so the message body is exactly the JSON the
Lambda published (no SNS envelope to unwrap).

Usage::

    # Poll forever; Ctrl+C to stop
    uv run src/scripts/consume_alerts.py

    # Exit after the first ``--max`` alerts (useful for CI smoke tests)
    uv run src/scripts/consume_alerts.py --max 1 --timeout 30
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QUEUE_NAME = "dos-boeing-737-max-alerts"


def _sqs_client():
    return boto3.client(
        "sqs",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _resolve_queue_url(sqs, name: str) -> str:
    """Look up the queue URL by name. Lets the script run without TF outputs."""
    try:
        return sqs.get_queue_url(QueueName=name)["QueueUrl"]
    except (BotoCoreError, ClientError) as exc:
        raise SystemExit(
            f"Could not find SQS queue {name!r} ({exc.__class__.__name__}). "
            f"Have you run `tflocal apply`?"
        )


def _format_alert(body: str) -> str:
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    frame = doc.get("frame", {})
    anomalies = doc.get("anomalies", [])
    types = ", ".join(a.get("type", "?") for a in anomalies)
    return (
        f"⚠️  flight={frame.get('flight_id', '?')} "
        f"seq_idx={frame.get('seq_idx', '?')} "
        f"types=[{types}]\n"
        f"   {json.dumps(doc, indent=2)}"
    )


def run(max_alerts: int | None, timeout: float | None) -> int:
    sqs = _sqs_client()
    queue_url = _resolve_queue_url(sqs, QUEUE_NAME)
    logger.info("Polling %s — Ctrl+C to stop", queue_url)

    deadline = (time.monotonic() + timeout) if timeout else None
    received = 0

    while True:
        if deadline is not None and time.monotonic() > deadline:
            logger.info("Timeout reached — exiting (received %d)", received)
            return received

        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=5,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("receive_message failed (%s) — retrying", exc.__class__.__name__)
            time.sleep(2)
            continue

        for msg in resp.get("Messages") or []:
            print(_format_alert(msg["Body"]), flush=True)
            try:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            except (BotoCoreError, ClientError) as exc:
                logger.warning("delete_message failed (%s)", exc.__class__.__name__)
            received += 1
            if max_alerts is not None and received >= max_alerts:
                logger.info("Max alerts reached — exiting (received %d)", received)
                return received


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max", dest="max_alerts", type=int, default=None,
                   help="Exit after this many alerts (default: run forever).")
    p.add_argument("--timeout", type=float, default=None,
                   help="Exit after this many seconds of total runtime.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        run(args.max_alerts, args.timeout)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
