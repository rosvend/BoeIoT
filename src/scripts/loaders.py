"""Data loading helpers with S3 (LocalStack) → local parquet fallback.

Lets the dashboard run even when LocalStack is down or the team hasn't
re-applied Terraform, by caching parquets to ``data/`` on first successful
S3 fetch and reading from there afterwards.
"""

import io
import logging
from pathlib import Path

import boto3
import pandas as pd
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

SILVER_BUCKET = "dos-boeing-737-max-silver-layer"
GOLD_BUCKET = "dos-boeing-737-max-gold-layer"
SILVER_KEY = "cleaned/flight_data_silver.parquet"
GOLD_KEY = "flight_summary.parquet"

# Repo-relative cache; resolved against the directory that contains src/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SILVER = PROJECT_ROOT / "data" / "silver" / "flight_data_silver.parquet"
LOCAL_GOLD = PROJECT_ROOT / "data" / "gold" / "flight_summary.parquet"


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url="http://localhost:4566",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name="us-east-1",
    )


def _try_s3_get(bucket: str, key: str) -> pd.DataFrame | None:
    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=bucket, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.info("S3 unavailable for s3://%s/%s (%s)", bucket, key, exc.__class__.__name__)
        return None


def _try_s3_put(bucket: str, key: str, df: pd.DataFrame) -> bool:
    try:
        s3 = _s3_client()
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
        return True
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.info("S3 put failed for s3://%s/%s (%s)", bucket, key, exc.__class__.__name__)
        return False


def _cache_locally(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def load_silver() -> tuple[pd.DataFrame, str]:
    """Return Silver dataframe and the source label ('s3' or 'local-cache')."""
    df = _try_s3_get(SILVER_BUCKET, SILVER_KEY)
    if df is not None:
        _cache_locally(df, LOCAL_SILVER)
        return df, "s3"
    if LOCAL_SILVER.exists():
        return pd.read_parquet(LOCAL_SILVER), "local-cache"
    raise FileNotFoundError(
        f"Silver no disponible. Levanta LocalStack y corre bronze_to_silver_etl.py, "
        f"o coloca el parquet manualmente en {LOCAL_SILVER}."
    )


def load_gold() -> tuple[pd.DataFrame, str]:
    """Return Gold dataframe and the source label.

    Resolution order:
        1. S3 (LocalStack)
        2. Local cache at data/gold/
        3. Compute inline from Silver (cached on disk for next run)
    """
    df = _try_s3_get(GOLD_BUCKET, GOLD_KEY)
    if df is not None:
        _cache_locally(df, LOCAL_GOLD)
        return df, "s3"
    if LOCAL_GOLD.exists():
        return pd.read_parquet(LOCAL_GOLD), "local-cache"

    logger.info("Gold no disponible; computando inline desde Silver...")
    from silver_to_gold_etl import compute_gold  # local import to avoid cycle at import time

    silver_df, silver_src = load_silver()
    logger.info("Silver cargado desde %s para cómputo inline", silver_src)
    gold_df = compute_gold(silver_df)
    _cache_locally(gold_df, LOCAL_GOLD)
    # Best-effort hydrate of S3 so the next consumer doesn't recompute either
    _try_s3_put(GOLD_BUCKET, GOLD_KEY, gold_df)
    return gold_df, "computed-inline"
