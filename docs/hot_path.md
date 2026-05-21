# Hot-path real-time anomaly pipeline

Streaming complement to the Bronze/Silver/Gold Medallion stack. Telemetry
frames flow through Kinesis, a Lambda runs a pluggable detector
(threshold-based by default), and anomalies fan out via SNS to an SQS
queue you can poll for a deterministic demo.

```
producer ──▶ Kinesis ──▶ Lambda ──▶ SNS ──▶ SQS ──▶ consumer
              telemetry-       (anomaly_detector,        alerts
              stream            DETECTOR_TYPE=threshold)  queue
```

Everything is provisioned by Terraform (`src/infrastructure/anomaly_pipeline.tf`)
and runs identically against LocalStack and real AWS — the only difference
is whether `AWS_ENDPOINT_URL` is set in the **caller's** environment.

---

## Resources

| Resource | Name | Defined in |
|---|---|---|
| Kinesis stream | `dos-boeing-737-max-telemetry-stream` | `anomaly_pipeline.tf` |
| SNS topic | `dos-boeing-737-max-anomalies` | `anomaly_pipeline.tf` |
| SQS queue (demo subscriber) | `dos-boeing-737-max-alerts` | `anomaly_pipeline.tf` |
| Lambda | `boeing_anomaly_detector` (Python 3.12) | `anomaly_pipeline.tf` |
| Event source mapping | Kinesis → Lambda, batch=10, window=1 s | `anomaly_pipeline.tf` |
| IAM inline policy | attached to `dos_mock_service_role` | `anomaly_pipeline.tf` |

Lambda env vars: `SNS_TOPIC_ARN`, `THRESHOLDS_BUCKET`, `THRESHOLDS_KEY`,
`DETECTOR_TYPE` (default `threshold`), `LOG_LEVEL`, optional `AWS_ENDPOINT_URL`.

---

## Anomaly payload schema

The producer emits frames in Silver's `[0, 1]` Min-Max-normalised space so
the same thresholds apply uniformly:

```json
{
  "flight_id": "F-00123",
  "seq_idx": 4217,
  "E1_RPM": 0.71,
  "E1_OilT": 0.62,
  "E1_OilP": 0.34,
  "E1_FFlow": 0.55,
  "cht_mean": 0.48, "cht_spread": 0.12,
  "egt_mean": 0.51, "egt_spread": 0.18,
  "fqty_total": 0.66, "fqty_balance": 0.02,
  "IAS": 0.45, "AltMSL": 0.30, "VSpd": 0.50,
  "flight_phase": "cruise"
}
```

The Lambda publishes to SNS only when at least one threshold is crossed.
The SNS→SQS subscription uses `raw_message_delivery = true`, so the SQS
message body is exactly the JSON below (no envelope):

```json
{
  "frame": { /* the input frame */ },
  "anomalies": [
    {"type": "high_oil_temp",   "value": 0.97, "threshold": 0.85},
    {"type": "low_oil_pressure","value": 0.05, "threshold": 0.10}
  ]
}
```

Detector kinds: `high_oil_temp`, `low_oil_pressure`, `cht_imbalance`, `egt_imbalance`.

---

## Thresholds — single source of truth

`silver_to_gold_etl.py` computes the four percentile thresholds
(`cht_spread_max`, `egt_spread_max`, `oil_press_min`, `oil_temp_max`) from
Silver, uses them for the Gold flag columns, **and** publishes them as JSON to
`s3://dos-boeing-737-max-silver-layer/artifacts/anomaly_thresholds.json`.

The Lambda loads that artifact on cold start
(`src/lambdas/anomaly_detector/config.py::load_thresholds`). If the artifact
is missing or malformed, it falls back to `DEFAULT_THRESHOLDS` in
`detector.py` — sensible physical defaults in `[0, 1]` space — so the demo
runs end-to-end even before the Silver→Gold ETL has been executed.

---

## Demo: produce → observe

```bash
# 1. Env
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1

# 2. Bring up infra
lstk start
cd src/infrastructure && tflocal init && tflocal apply -auto-approve && cd ../..

# 3. (Optional) hydrate Silver + publish data-driven thresholds artifact
uv run src/scripts/ingest_bronze.py
uv run src/scripts/bronze_to_silver_etl.py
uv run src/scripts/silver_to_gold_etl.py

# 4. Smoke-check the resources
aws --endpoint-url=$AWS_ENDPOINT_URL kinesis list-streams
aws --endpoint-url=$AWS_ENDPOINT_URL sns list-topics
aws --endpoint-url=$AWS_ENDPOINT_URL sqs list-queues
aws --endpoint-url=$AWS_ENDPOINT_URL lambda list-functions

# 5. Drive an alert end-to-end (two terminals)
#    Terminal A — consumer
uv run src/scripts/consume_alerts.py
#    Terminal B — producer, force an oil-temp anomaly every frame
uv run src/scripts/produce_telemetry.py --inject-anomaly oil_temp --count 5 --rate 1

# 6. Inspect Lambda logs (LocalStack mirrors CloudWatch)
aws --endpoint-url=$AWS_ENDPOINT_URL logs tail /aws/lambda/boeing_anomaly_detector --follow
```

Expected: within ~5 seconds of the producer firing, the consumer prints one
or more `⚠️ flight=... types=[high_oil_temp]` lines.

Run `uv run src/scripts/produce_telemetry.py --count 20` without
`--inject-anomaly` to confirm normal frames produce zero alerts.

---

## Tests

```bash
uv sync                # picks up the new pytest dev dep
uv run pytest -v
```

No AWS calls — `tests/test_detector.py` exercises `ThresholdDetector` in
isolation; `tests/test_config.py` mocks the S3 client.

---

## Portability to real AWS

No code changes. Unset `AWS_ENDPOINT_URL`, point `aws_region` at the real
region, run `terraform apply` against an AWS account that the role
`dos_mock_service_role` is allowed to assume. The IAM inline policy already
scopes Kinesis/SNS/S3 actions to the specific resource ARNs.
