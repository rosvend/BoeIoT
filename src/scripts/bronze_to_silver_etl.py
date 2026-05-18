import pandas as pd
import numpy as np
import boto3
import io
import joblib
from sklearn.preprocessing import MinMaxScaler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Initializing ETL: Bronze -> Silver (EDA-guided transformations)")

# ── INFRASTRUCTURE ────────────────────────────────────────────────────────────
s3 = boto3.client(
    's3',
    endpoint_url='http://localhost:4566',
    aws_access_key_id='test',
    aws_secret_access_key='test',
    region_name='us-east-1'
)

bronze_bucket = "dos-boeing-737-max-bronze-layer"
silver_bucket = "dos-boeing-737-max-silver-layer"
raw_key      = "raw/flight_data.parquet"
clean_key    = "cleaned/flight_data_silver.parquet"
scaler_key   = "artifacts/minmax_scaler.joblib"

# ── EXTRACT ───────────────────────────────────────────────────────────────────
logger.info(f"Extracting data from s3://{bronze_bucket}/{raw_key}")
response = s3.get_object(Bucket=bronze_bucket, Key=raw_key)
df = pd.read_parquet(io.BytesIO(response['Body'].read()))
logger.info(f"Loaded {len(df):,} rows, {df.shape[1]} columns.")

# ── TRANSFORM ─────────────────────────────────────────────────────────────────

# Step 1: Capture sensor availability per flight BEFORE any imputation.
# Measures what fraction of sensor readings were originally valid in each flight.
# EDA showed ~5.88% systematic nulls in 5 sensors from a bus failure.
sensor_cols_bronze = [c for c in df.columns if c != 'flight_id']
flight_availability = (
    df.groupby('flight_id')[sensor_cols_bronze]
    .apply(lambda g: g.notna().mean().mean())
    .rename('sensor_availability')
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
SYSTEMIC_NULL_COLS = ['E1_CHT2', 'E1_CHT3', 'E1_CHT4', 'amp2', 'volt2']
systemic_present = [c for c in SYSTEMIC_NULL_COLS if c in df.columns]
df[systemic_present] = (
    df.groupby('flight_id')[systemic_present]
    .transform(lambda x: x.ffill().bfill())
)
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

# Step 11: Min-Max normalize all numeric sensor features.
# Metadata columns (IDs, categorical, counts) are excluded from scaling.
META_COLS = {'flight_duration_sec', 'sensor_availability'}
scale_cols = [c for c in numeric_cols if c not in META_COLS]
scaler = MinMaxScaler()
df_clean[scale_cols] = scaler.fit_transform(df_clean[scale_cols])
logger.info(f"Normalized {len(scale_cols)} columns to [0, 1] with MinMaxScaler.")

# Step 12: Persist scaler to S3 so future runs use the same scale reference.
# Without this, re-running fit_transform on new data shifts the scale and
# breaks any Gold models trained on the original distribution.
scaler_buffer = io.BytesIO()
joblib.dump(scaler, scaler_buffer)
s3.put_object(Bucket=silver_bucket, Key=scaler_key, Body=scaler_buffer.getvalue())
logger.info(f"Scaler persisted → s3://{silver_bucket}/{scaler_key}")

# ── LOAD ──────────────────────────────────────────────────────────────────────
logger.info(f"Loading {len(df_clean):,} rows → s3://{silver_bucket}/{clean_key}")
out_buffer = io.BytesIO()
df_clean.to_parquet(out_buffer, index=False)
s3.put_object(Bucket=silver_bucket, Key=clean_key, Body=out_buffer.getvalue())

logger.info("ETL Bronze → Silver completed successfully!")
logger.info(f"Silver schema ({df_clean.shape[1]} cols): {list(df_clean.columns)}")
logger.info(
    df_clean[['flight_id', 'E1_RPM', 'E1_OilT', 'IAS', 'flight_phase', 'cht_mean', 'egt_mean']].head()
)
