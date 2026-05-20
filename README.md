# BoeIoT

**BoeIoT** is an end-to-end IoT analytics solution designed to predict component failures and flight anomalies. By leveraging real-time sensor data and a Medallion data architecture, it enables proactive maintenance, reducing downtime and enhancing flight safety.
The platform utilizes a **Lakehouse** pattern deployed on AWS, facilitating both high-volume batch processing and low-latency event handling.

---

## Data architecture

![Data architecture](docs/DOS%20-%20AWS%20Data%20architecture.jpg)

### The Medallion Approach

We follow the **Medallion Architecture** to ensure data quality and lineage:

* **Bronze (Raw):** Ingests raw telemetry and sensor streams in their original format.
* **Silver (Validated):** Cleans, filters, and joins data to create a "source of truth."
* **Gold (Enriched):** Aggregates data into features and KPIs optimized for Machine Learning models and executive dashboards.


## 🛠️ Tech Stack
- **Languages:** Python (>=3.12)
- **Infrastructure & Cloud:** Terraform, LocalStack (Local AWS Mock)
- **Data Engineering & Analytics:** Pandas, PyArrow, Scikit-learn, Matplotlib, Seaborn
- **Cloud SDK:** Boto3
- **Package Management:** uv

## 🚀 How to Run

### Prerequisites
Make sure you have the following installed on your machine:
- [Docker](https://www.docker.com/)
- [LocalStack](https://github.com/localstack/lstk)
- [Terraform](https://www.terraform.io/)
- Python 3.12+ and [uv](https://github.com/astral-sh/uv)

### 1. Start Local Infrastructure
First, start LocalStack to mock the AWS services:
```bash
lstk start
```

Then, initialize and apply the Terraform configuration to provision the necessary services (e.g., S3 buckets):
```bash
cd src/infrastructure
terraform init
terraform apply -auto-approve
cd ../..
```

### 2. Set Up the Python Environment
Use `uv` to install the dependencies and sync the environment:
```bash
uv sync
source .venv/bin/activate
```

### 3. Run Data Pipelines

The S3 clients in `ingest_bronze.py`, `bronze_to_silver_etl.py` and `loaders.py` are env-driven so the same code runs against LocalStack and real AWS. For local runs export:
```bash
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
```
On AWS (e.g. Glue) leave `AWS_ENDPOINT_URL` unset — the SDK uses the IAM role.

With the infrastructure up and the environment active, run the pipelines:
```bash
# 1. Ingest raw data into the Bronze layer
uv run src/scripts/ingest_bronze.py

# 2. Clean and consolidate data into the Silver layer
uv run src/scripts/bronze_to_silver_etl.py

# 3. Aggregate per-flight KPIs into the Gold layer
uv run src/scripts/silver_to_gold_etl.py
```

### 4. Maintenance dashboard
The Jupyter dashboard reads the Gold layer with a three-stage fallback (S3 → `data/gold/` local cache → inline compute from Silver), so it runs even with LocalStack down:
```bash
uv run jupyter notebook src/notebooks/dashboard_maintenance.ipynb
```
Schema, KPI definitions and the engine health score formula are documented in [`docs/gold_to_dashboard.md`](docs/gold_to_dashboard.md).

### 5. Hot-path anomaly pipeline (Kinesis → Lambda → SNS)
A streaming complement to the batch Medallion stack: telemetry frames flow through Kinesis, a Lambda runs a pluggable detector (threshold-based by default, swappable for an ONNX/sklearn model), and anomalies fan out via SNS to an SQS queue you can poll deterministically.

```bash
# Terminal A — start the consumer
uv run src/scripts/consume_alerts.py

# Terminal B — force one anomaly type every frame
uv run src/scripts/produce_telemetry.py --inject-anomaly oil_temp --count 5
```

Full walkthrough, payload schema, and the data-driven thresholds artifact (written by `silver_to_gold_etl.py`) are in [`docs/hot_path.md`](docs/hot_path.md). Unit tests:
```bash
uv sync && uv run pytest -v
```

---
