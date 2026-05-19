import pandas as pd
import numpy as np
import boto3
import io
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Initializing ETL: Silver -> Gold (flight_summary)")

# ── INFRASTRUCTURE ────────────────────────────────────────────────────────────
s3 = boto3.client(
    's3',
    endpoint_url='http://localhost:4566',
    aws_access_key_id='test',
    aws_secret_access_key='test',
    region_name='us-east-1'
)

silver_bucket = "dos-boeing-737-max-silver-layer"
gold_bucket   = "dos-boeing-737-max-gold-layer"
silver_key    = "cleaned/flight_data_silver.parquet"
gold_key      = "flight_summary.parquet"

# ── EXTRACT ───────────────────────────────────────────────────────────────────
logger.info(f"Extracting Silver from s3://{silver_bucket}/{silver_key}")
response = s3.get_object(Bucket=silver_bucket, Key=silver_key)
df = pd.read_parquet(io.BytesIO(response['Body'].read()))
logger.info(f"Loaded {len(df):,} rows, {df.shape[1]} cols, {df['flight_id'].nunique()} flights.")

# ── THRESHOLDS (global percentiles over Silver) ───────────────────────────────
# Silver values are normalized to [0,1], so percentiles describe the
# distribution shape rather than physical units.
cht_p95   = df['cht_spread'].quantile(0.95)
egt_p95   = df['egt_spread'].quantile(0.95)
oilp_p05  = df['E1_OilP'].quantile(0.05)
oilt_p95  = df['E1_OilT'].quantile(0.95)
logger.info(
    f"Anomaly thresholds — cht_spread>{cht_p95:.3f}, egt_spread>{egt_p95:.3f}, "
    f"E1_OilP<{oilp_p05:.3f}, E1_OilT>{oilt_p95:.3f}"
)

# Row-level boolean flags for anomaly counting
df['flag_cht_imbalance']     = df['cht_spread'] > cht_p95
df['flag_egt_imbalance']     = df['egt_spread'] > egt_p95
df['flag_low_oil_pressure']  = df['E1_OilP'] < oilp_p05
df['flag_high_oil_temp']     = df['E1_OilT'] > oilt_p95

# ── AGGREGATE ─────────────────────────────────────────────────────────────────
logger.info("Aggregating per-flight KPIs...")
agg = df.groupby('flight_id').agg(
    flight_duration_sec=('flight_duration_sec', 'first'),
    sensor_availability=('sensor_availability', 'first'),
    avg_rpm=('E1_RPM', 'mean'),
    max_rpm=('E1_RPM', 'max'),
    std_rpm=('E1_RPM', 'std'),
    avg_oil_temp=('E1_OilT', 'mean'),
    max_oil_temp=('E1_OilT', 'max'),
    avg_oil_pressure=('E1_OilP', 'mean'),
    min_oil_pressure=('E1_OilP', 'min'),
    avg_cht_mean=('cht_mean', 'mean'),
    max_cht_max=('cht_max', 'max'),
    avg_cht_spread=('cht_spread', 'mean'),
    max_cht_spread=('cht_spread', 'max'),
    avg_egt_mean=('egt_mean', 'mean'),
    max_egt_max=('egt_max', 'max'),
    avg_egt_spread=('egt_spread', 'mean'),
    max_egt_spread=('egt_spread', 'max'),
    avg_fuel_flow=('E1_FFlow', 'mean'),
    max_fuel_imbalance=('fqty_balance', lambda x: x.abs().max()),
    max_altitude=('AltMSL', 'max'),
    max_ias=('IAS', 'max'),
    max_descent_rate=('VSpd', 'min'),
    cht_imbalance_events=('flag_cht_imbalance', 'sum'),
    egt_imbalance_events=('flag_egt_imbalance', 'sum'),
    low_oil_pressure_events=('flag_low_oil_pressure', 'sum'),
    high_oil_temp_events=('flag_high_oil_temp', 'sum'),
).reset_index()

# Fuel consumed = first reading - last reading of fqty_total (per flight)
# Assumes rows are in chronological order within each flight_id group.
fuel_consumed = (
    df.groupby('flight_id')['fqty_total']
    .agg(lambda x: x.iloc[0] - x.iloc[-1])
    .rename('fuel_consumed')
    .reset_index()
)
agg = agg.merge(fuel_consumed, on='flight_id', how='left')

# Phase distribution per flight (% of rows in each phase)
phase_pct = pd.crosstab(df['flight_id'], df['flight_phase'], normalize='index')
phase_pct = phase_pct.rename(columns=lambda c: f'pct_{c}').reset_index()
for col in ['pct_ascent', 'pct_cruise', 'pct_descent']:
    if col not in phase_pct.columns:
        phase_pct[col] = 0.0
agg = agg.merge(phase_pct[['flight_id', 'pct_ascent', 'pct_cruise', 'pct_descent']], on='flight_id', how='left')
agg[['pct_ascent', 'pct_cruise', 'pct_descent']] = agg[['pct_ascent', 'pct_cruise', 'pct_descent']].fillna(0)

# Average altitude restricted to cruise phase
cruise_alt = (
    df[df['flight_phase'] == 'cruise']
    .groupby('flight_id')['AltMSL']
    .mean()
    .rename('avg_cruise_altitude')
    .reset_index()
)
agg = agg.merge(cruise_alt, on='flight_id', how='left')
agg['avg_cruise_altitude'] = agg['avg_cruise_altitude'].fillna(0)

# Derived: duration in minutes for readability
agg['flight_duration_min'] = agg['flight_duration_sec'] / 60

# Total anomaly count
agg['total_anomaly_count'] = (
    agg['cht_imbalance_events']
    + agg['egt_imbalance_events']
    + agg['low_oil_pressure_events']
    + agg['high_oil_temp_events']
)

# ── ENGINE HEALTH SCORE (0-100) ───────────────────────────────────────────────
# Weighted combination where each component is in [0,1] and 1 = healthy.
max_anom = agg['total_anomaly_count'].max() or 1
anom_norm = agg['total_anomaly_count'] / max_anom

spread_avg = (agg['max_cht_spread'] + agg['max_egt_spread']) / 2

agg['engine_health_score'] = 100 * (
    0.30 * (1 - anom_norm)
    + 0.25 * (1 - spread_avg)
    + 0.20 * (1 - agg['max_oil_temp'])
    + 0.15 * agg['min_oil_pressure']
    + 0.10 * agg['sensor_availability']
)
agg['engine_health_score'] = agg['engine_health_score'].clip(0, 100)

# ── REORDER COLUMNS ───────────────────────────────────────────────────────────
column_order = [
    'flight_id',
    'flight_duration_sec', 'flight_duration_min', 'sensor_availability',
    'pct_ascent', 'pct_cruise', 'pct_descent',
    'avg_rpm', 'max_rpm', 'std_rpm',
    'avg_oil_temp', 'max_oil_temp', 'avg_oil_pressure', 'min_oil_pressure',
    'avg_cht_mean', 'max_cht_max', 'avg_cht_spread', 'max_cht_spread',
    'avg_egt_mean', 'max_egt_max', 'avg_egt_spread', 'max_egt_spread',
    'fuel_consumed', 'avg_fuel_flow', 'max_fuel_imbalance',
    'max_altitude', 'avg_cruise_altitude', 'max_ias', 'max_descent_rate',
    'cht_imbalance_events', 'egt_imbalance_events',
    'low_oil_pressure_events', 'high_oil_temp_events', 'total_anomaly_count',
    'engine_health_score',
]
agg = agg[column_order]

# ── LOAD ──────────────────────────────────────────────────────────────────────
logger.info(f"Loading {len(agg)} flight summaries → s3://{gold_bucket}/{gold_key}")
out_buffer = io.BytesIO()
agg.to_parquet(out_buffer, index=False)
s3.put_object(Bucket=gold_bucket, Key=gold_key, Body=out_buffer.getvalue())

logger.info("ETL Silver → Gold completed successfully!")
logger.info(f"Gold schema ({agg.shape[1]} cols): {list(agg.columns)}")
logger.info(
    f"Health score stats: min={agg['engine_health_score'].min():.1f}, "
    f"mean={agg['engine_health_score'].mean():.1f}, max={agg['engine_health_score'].max():.1f}"
)
logger.info(agg[['flight_id', 'flight_duration_min', 'total_anomaly_count', 'engine_health_score']].head())
