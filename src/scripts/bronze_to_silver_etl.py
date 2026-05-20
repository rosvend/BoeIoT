import os
import pandas as pd
import numpy as np
import sys
import boto3
import io
import joblib
from botocore.exceptions import ClientError
from sklearn.preprocessing import MinMaxScaler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Initializing ETL: Bronze -> Silver (EDA-guided transformations)")

# ── INFRASTRUCTURE ────────────────────────────────────────────────────────────
# Env-driven config so the same script runs against LocalStack and real AWS.
# Local dev: export AWS_ENDPOINT_URL=http://localhost:4566 and
# AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY=test. On AWS (Glue) leave
# AWS_ENDPOINT_URL unset; boto3 falls back to the IAM role from mock_service_role.
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

bronze_bucket = "dos-boeing-737-max-bronze-layer"
silver_bucket = "dos-boeing-737-max-silver-layer"
raw_key      = "raw/flight_data.parquet"
clean_key    = "cleaned/flight_data_silver.parquet"
scaler_key   = "artifacts/minmax_scaler.joblib"

# ── EXTRACT ───────────────────────────────────────────────────────────────────
logger.info(f"Extracting data from s3://{bronze_bucket}/{raw_key}")
try:
    response = s3.get_object(Bucket=bronze_bucket, Key=raw_key)
except s3.exceptions.NoSuchKey:
    logger.error(f"Bronze object missing: s3://{bronze_bucket}/{raw_key}. "
                 f"Run ingest_bronze.py first.")
    sys.exit(1)
except ClientError as e:
    logger.error(f"S3 error reading Bronze: {e}")
    sys.exit(1)

df = pd.read_parquet(io.BytesIO(response['Body'].read()))
logger.info(f"Loaded {len(df):,} rows, {df.shape[1]} columns.")

# Step 0: Deterministic row order before any order-dependent transforms.
# ingest_bronze.py materializes seq_idx (chronological position within each flight)
# because Parquet does not preserve row order. ffill/bfill, diff, and rolling
# all depend on this ordering.
if 'seq_idx' in df.columns:
    df = df.sort_values(['flight_id', 'seq_idx']).reset_index(drop=True)
    logger.info("Rows sorted by (flight_id, seq_idx) — order-dependent ops are safe.")
else:
    logger.warning("No seq_idx column in Bronze — assuming rows are already in "
                   "chronological order. Re-run ingest_bronze.py to materialize seq_idx.")

# ── TRANSFORM ─────────────────────────────────────────────────────────────────

# Step 1: Capture sensor availability per flight BEFORE any imputation.
# Measures what fraction of sensor readings were originally valid in each flight.
# EDA showed ~5.88% systematic nulls in 5 sensors from a bus failure.
# Vectorized: avoid groupby.apply(lambda) for performance.
SENSOR_EXCLUDE = {'flight_id', 'seq_idx'}
sensor_cols_bronze = [c for c in df.columns if c not in SENSOR_EXCLUDE]
row_availability = df[sensor_cols_bronze].notna().mean(axis=1)
flight_availability = (
    row_availability.groupby(df['flight_id']).mean().rename('sensor_availability')
)
df = df.join(flight_availability, on='flight_id')

# Step 2: Flight duration (rows at 1 Hz sampling = seconds).
flight_duration = df.groupby('flight_id').size().rename('flight_duration_sec')
df = df.join(flight_duration, on='flight_id')

# Step 3: Clip physically impossible values (EDA identified negative pressures,
# altitudes, and airspeeds caused by pre-flight sensor initialization at ground).
PHYSICAL_CLIPS = {
    'E1_OilP': (0, None),
    'IAS':     (0, None),
    'AltMSL':  (0, None),
    'VSpd':    (-3000, 3000),
}
for col, (lo, hi) in PHYSICAL_CLIPS.items():
    if col in df.columns:
        df[col] = df[col].clip(lower=lo, upper=hi)
        logger.info(f"Clipped {col} → [{lo}, {hi}]")

# Step 4: Impute systemic nulls via forward-fill then back-fill within each flight.
# EDA showed E1_CHT2/3/4, amp2, volt2 all share ~5.88% nulls simultaneously —
# a single telemetry bus dropout, not random sensor failure. ffill/bfill
# recovers these gaps from adjacent valid readings within the same flight.
# Requires the row sort in Step 0 to be meaningful.
SYSTEMIC_NULL_COLS = ['E1_CHT2', 'E1_CHT3', 'E1_CHT4', 'amp2', 'volt2']
systemic_present = [c for c in SYSTEMIC_NULL_COLS if c in df.columns]
if systemic_present:
    # Two separate groupby calls are required here.
    # groupby().ffill() returns a plain DataFrame (not a GroupBy object), so
    # chaining .bfill() on the result would backfill globally across all flights,
    # leaking the last sensor value of flight N into the leading NaN rows of
    # flight N+1. Two independent groupby calls keep each operation per-flight.
    df[systemic_present] = df.groupby('flight_id')[systemic_present].ffill()
    df[systemic_present] = df.groupby('flight_id')[systemic_present].bfill()
    logger.info(f"Imputed systemic nulls (ffill/bfill) in: {systemic_present}")

# Step 5: Drop volt2 — r=0.993 with volt1, VIF≈54,000 (EDA correlation analysis).
# volt1 is kept as the representative electrical voltage measurement.
if 'volt2' in df.columns:
    df.drop(columns=['volt2'], inplace=True)
    logger.info("Dropped redundant column: volt2 (r=0.993 with volt1)")

# Step 6: Collapse CHT sensors into aggregated features.
# EDA: r>0.94 between all 4 cylinders, VIF 295–806. The aggregates preserve
# the diagnostic signal while eliminating the redundant individual readings.
# cht_spread is the key anomaly indicator (uneven cylinder temps → misfires).
cht_cols = ['E1_CHT1', 'E1_CHT2', 'E1_CHT3', 'E1_CHT4']
cht_present = [c for c in cht_cols if c in df.columns]
if len(cht_present) >= 2:
    df['cht_mean']   = df[cht_present].mean(axis=1)
    df['cht_std']    = df[cht_present].std(axis=1)
    df['cht_max']    = df[cht_present].max(axis=1)
    df['cht_min']    = df[cht_present].min(axis=1)
    df['cht_spread'] = df['cht_max'] - df['cht_min']
    df.drop(columns=cht_present, inplace=True)
    logger.info(f"Collapsed {cht_present} → cht_mean/std/max/min/spread")

# Step 7: Collapse EGT sensors into aggregated features.
# EDA: r>0.95, VIF 600–1,480. egt_spread indicates uneven combustion.
egt_cols = ['E1_EGT1', 'E1_EGT2', 'E1_EGT3', 'E1_EGT4']
egt_present = [c for c in egt_cols if c in df.columns]
if len(egt_present) >= 2:
    df['egt_mean']   = df[egt_present].mean(axis=1)
    df['egt_std']    = df[egt_present].std(axis=1)
    df['egt_max']    = df[egt_present].max(axis=1)
    df['egt_min']    = df[egt_present].min(axis=1)
    df['egt_spread'] = df['egt_max'] - df['egt_min']
    df.drop(columns=egt_present, inplace=True)
    logger.info(f"Collapsed {egt_present} → egt_mean/std/max/min/spread")

# Step 8: Collapse fuel quantity tanks into total + balance.
# fqty_balance detects asymmetric fuel consumption (fuel leak or imbalance fault).
if 'FQtyL' in df.columns and 'FQtyR' in df.columns:
    df['fqty_total']   = df['FQtyL'] + df['FQtyR']
    df['fqty_balance'] = df['FQtyL'] - df['FQtyR']
    df.drop(columns=['FQtyL', 'FQtyR'], inplace=True)
    logger.info("Collapsed FQtyL/FQtyR → fqty_total/fqty_balance")

# Step 9: Create flight_phase label based on smoothed AltMSL derivative.
# EDA revealed 3 distinct operational regimes (ascent/cruise/descent) with
# multimodal RPM and IAS distributions — global normalization conflates them.
# Threshold: 50 ft/s smoothed over 30s rolling window separates regimes cleanly.
# Requires the row sort in Step 0 to produce a meaningful time derivative.
ALT_CLIMB_THRESHOLD = 50  # ft/s
if 'AltMSL' in df.columns:
    dalt_smooth = (
        df.groupby('flight_id')['AltMSL']
        .transform(lambda x: x.diff().rolling(window=30, min_periods=1, center=True).mean())
    )
    df['flight_phase'] = np.select(
        [dalt_smooth > ALT_CLIMB_THRESHOLD, dalt_smooth < -ALT_CLIMB_THRESHOLD],
        ['ascent', 'descent'],
        default='cruise'
    )
    phase_counts = df['flight_phase'].value_counts().to_dict()
    logger.info(f"Created flight_phase: {phase_counts}")

# Step 10: Drop remaining NaN rows on numeric columns only.
# After imputing systemic nulls and dropping volt2, residual NaNs are
# truly random sensor failures (<0.3% per column) — safe to discard.
initial_rows = len(df)
numeric_cols = df.select_dtypes(include='number').columns.tolist()
df_clean = df.dropna(subset=numeric_cols).copy()
logger.info(f"Dropped {initial_rows - len(df_clean):,} rows (residual random NaNs).")

# Step 11: Min-Max normalize numeric sensor features.
# META_COLS lists every column that must NOT be scaled. Includes flight_id
# (defensive against future int-ID refactors), seq_idx (row ordering key),
# flight_phase (categorical), and the derived flight-level metadata.
META_COLS = {
    'flight_id',
    'seq_idx',
    'flight_duration_sec',
    'sensor_availability',
    'flight_phase',
}
scale_cols = [c for c in numeric_cols if c not in META_COLS]

# Scaler load-or-fit pattern: reuse persisted scaler when available so the
# scale stays consistent across runs (critical if Gold models depend on it).
# Only on cold start (no scaler in S3) do we fit a new one.
try:
    logger.info(f"Attempting to load existing scaler from s3://{silver_bucket}/{scaler_key}")
    scaler_response = s3.get_object(Bucket=silver_bucket, Key=scaler_key)
    scaler = joblib.load(io.BytesIO(scaler_response['Body'].read()))
    # Schema-drift guard: if Bronze grew/shrunk a sensor since the persisted
    # scaler was fit, transform() raises ValueError on the column count or
    # ordering mismatch. Detect via feature_names_in_ and refit instead of
    # letting the run abort halfway.
    fitted_features = list(getattr(scaler, "feature_names_in_", []))
    if fitted_features and fitted_features != scale_cols:
        logger.warning(
            "Schema drift detected — persisted scaler features %s != current "
            "scale_cols %s. Fitting a fresh MinMaxScaler.",
            fitted_features, scale_cols,
        )
        scaler = MinMaxScaler()
        df_clean[scale_cols] = scaler.fit_transform(df_clean[scale_cols])
        persist_scaler = True
    else:
        df_clean[scale_cols] = scaler.transform(df_clean[scale_cols])
        persist_scaler = False
        logger.info("Existing scaler loaded — applied transform() for cross-run consistency.")
except s3.exceptions.NoSuchKey:
    logger.info("No persisted scaler found — fitting a new MinMaxScaler (cold start).")
    scaler = MinMaxScaler()
    df_clean[scale_cols] = scaler.fit_transform(df_clean[scale_cols])
    persist_scaler = True
except ClientError as e:
    logger.error(f"Unexpected S3 error while loading scaler: {e}")
    sys.exit(1)

logger.info(f"Normalized {len(scale_cols)} columns to [0, 1].")

# ── LOAD ──────────────────────────────────────────────────────────────────────
# Write Parquet FIRST. If it fails, the scaler is not (re)persisted, which
# preserves consistency between the artifact and the dataset that uses it.
logger.info(f"Loading {len(df_clean):,} rows → s3://{silver_bucket}/{clean_key}")
out_buffer = io.BytesIO()
df_clean.to_parquet(out_buffer, index=False)
try:
    s3.put_object(Bucket=silver_bucket, Key=clean_key, Body=out_buffer.getvalue())
except ClientError as e:
    logger.error(f"Silver Parquet write failed for s3://{silver_bucket}/{clean_key}: {e}")
    sys.exit(1)

# Persist the scaler only on cold start AND only after the Parquet succeeded.
if persist_scaler:
    scaler_buffer = io.BytesIO()
    joblib.dump(scaler, scaler_buffer)
    try:
        s3.put_object(Bucket=silver_bucket, Key=scaler_key, Body=scaler_buffer.getvalue())
        logger.info(f"Scaler persisted → s3://{silver_bucket}/{scaler_key}")
    except ClientError as e:
        logger.error(f"Scaler persistence failed for s3://{silver_bucket}/{scaler_key}: {e}")
        sys.exit(1)
else:
    logger.info("Scaler not re-persisted (existing artifact still matches the data).")

logger.info("ETL Bronze → Silver completed successfully!")
logger.info(f"Silver schema ({df_clean.shape[1]} cols): {list(df_clean.columns)}")

# Guarded preview: select only the columns that actually exist to avoid
# a KeyError after the dataset was already written.
preview_candidates = ['flight_id', 'seq_idx', 'E1_RPM', 'E1_OilT', 'IAS',
                      'flight_phase', 'cht_mean', 'egt_mean']
preview_cols = [c for c in preview_candidates if c in df_clean.columns]
logger.info(df_clean[preview_cols].head())
