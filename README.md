# BoeIoT — IoT Analytics platform for airplane predictive maintenance

**BoeIoT** is an end-to-end analytics solution that processes aircraft sensor telemetry to detect anomalies and predict component failures. It implements a Medallion architecture (Bronze → Silver → Gold) on AWS, combining batch processing with interactive per-layer dashboards.

---

## Data architecture

![AWS architecture](docs/img/DOS%20-%20AWS%20Data%20architecture.drawio.png)

The project implements the **Medallion Architecture** pattern over an S3 Data Lake:

| Layer | Script | Description |
|-------|--------|-------------|
| **Bronze (Raw)** | `ingest_bronze.py` | Ingests raw data from the NGAFID dataset (50 flights, ~23 sensors, 1 Hz sampling). No transformations: preserves the original state. |
| **Silver (Validated)** | `bronze_to_silver_etl.py` | Deduplication, clipping of physically impossible values, forward/backward fill imputation for systemic nulls, collapse of redundant sensors (CHT/EGT), per-sensor MinMax normalization. |
| **Gold (Enriched)** | `silver_to_gold_etl.py` | Aggregates at flight level: 35 KPIs per `flight_id` including Engine Health Score (0–100), anomaly counts, fuel consumption, flight-phase distribution, and oil temperature/pressure statistics. |

The **Engine Health Score** combines: detected anomalies (30%), CHT cylinder spread (25%), oil temperature (20%), oil pressure (15%), and sensor availability (10%).

Anomaly thresholds are computed per global percentile (high p95 / low p05), capturing the most extreme 5% of readings per sensor.

A streaming complement to the batch stack, the **hot-path anomaly pipeline** routes telemetry frames through Kinesis, where a Lambda runs a pluggable detector (threshold-based by default, swappable for an ONNX/sklearn model) and fans anomalies out via SNS to an SQS queue.

---

## Quick start

### Requirements

- [Docker](https://www.docker.com/)
- [LocalStack CLI](https://github.com/localstack/localstack) (`pip install localstack`)
- [Terraform](https://www.terraform.io/) >= 1.0
- Python 3.12+ and [uv](https://github.com/astral-sh/uv)

### 1. Bring up the local infrastructure

Start LocalStack to simulate AWS services (S3, Glue, Athena, Lambda):

```bash
localstack start
```

Provision the resources with Terraform:

```bash
cd src/infrastructure
terraform init
terraform apply -auto-approve
cd ../..
```

### 2. Set up the Python environment

```bash
uv sync
```

### 3. Run the ETL pipeline

The S3 clients in `ingest_bronze.py`, `bronze_to_silver_etl.py` and `loaders.py` are configured through environment variables, so the same code works against LocalStack and real AWS. For local runs, export:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
```

On AWS (e.g. Glue) leave `AWS_ENDPOINT_URL` unset — the SDK uses the IAM role.

With the infrastructure up and the environment ready, run the pipelines in order:

```bash
# Bronze layer: download and load raw data from Kaggle (NGAFID dataset)
uv run src/scripts/ingest_bronze.py

# Silver layer: cleaning, normalization and feature engineering
uv run src/scripts/bronze_to_silver_etl.py

# Gold layer: per-flight aggregation and KPI computation
uv run src/scripts/silver_to_gold_etl.py
```

### 4. Run the hot-path anomaly pipeline (Kinesis → Lambda → SNS)

```bash
# Terminal A — start the consumer
uv run src/scripts/consume_alerts.py

# Terminal B — force one anomaly type every frame
uv run src/scripts/produce_telemetry.py --inject-anomaly oil_temp --count 5
```

The full walkthrough, payload schema, and the data-driven thresholds artifact (written by `silver_to_gold_etl.py`) are in [`docs/hot_path.md`](docs/hot_path.md).

Run the unit tests with:

```bash
uv sync && uv run pytest -v
```
