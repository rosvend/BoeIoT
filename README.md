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
With the infrastructure up and the environment active, you can now run the pipelines:
```bash
# 1. Ingest raw data into the Bronze layer
uv run src/scripts/ingest_bronze.py

# 2. Clean and consolidate data into the Silver layer
uv run src/scripts/bronze_to_silver_etl.py
```

---
