"""Silver → Gold ETL: aggregates per-second telemetry into per-flight KPIs.

``compute_gold(df)`` is exposed as a pure function so the dashboard notebook
can compute Gold inline when LocalStack is not available.

Run as a script to materialise Gold to the S3 Gold bucket (and a local cache),
plus publish the anomaly-threshold artifact consumed by the hot-path Lambda
(see ``src/lambdas/anomaly_detector/config.py``).
"""

import json
import logging
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

# Where the hot-path Lambda reads thresholds from. Kept in sync with
# ``anomaly_pipeline.tf`` (THRESHOLDS_BUCKET / THRESHOLDS_KEY env vars).
THRESHOLDS_KEY = "artifacts/anomaly_thresholds.json"


def _compute_thresholds(df: pd.DataFrame) -> dict[str, float]:
    """Return the four percentile anomaly thresholds computed from Silver.

    Single source of truth used by both ``compute_gold`` (for in-place flag
    columns) and ``_publish_thresholds`` (for the hot-path Lambda artifact).
    """
    return {
        "cht_spread_max": float(df["cht_spread"].quantile(0.95)),
        "egt_spread_max": float(df["egt_spread"].quantile(0.95)),
        "oil_press_min":  float(df["E1_OilP"].quantile(0.05)),
        "oil_temp_max":   float(df["E1_OilT"].quantile(0.95)),
    }


def _publish_thresholds(s3_client, bucket: str, key: str, thresholds: dict[str, float]) -> bool:
    """Write the thresholds JSON to S3. Returns True on success, False on failure.

    Failure is logged but never raised — the same "best-effort S3, don't break
    the pipeline" stance taken by ``loaders._try_s3_put``.
    """
    body = json.dumps(
        {
            "version": 1,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "silver_to_gold_etl._compute_thresholds",
            "thresholds": thresholds,
        },
        indent=2,
    ).encode("utf-8")
    try:
        s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
        logger.info("Thresholds artifact published → s3://%s/%s", bucket, key)
        return True
    except Exception as exc:  # pragma: no cover — best-effort publish
        logger.info(
            "Thresholds publish failed for s3://%s/%s (%s) — Lambda will use DEFAULT_THRESHOLDS",
            bucket, key, exc.__class__.__name__,
        )
        return False


def compute_gold(df: pd.DataFrame) -> pd.DataFrame:
    """Build per-flight Gold summary from a Silver dataframe.

    The Silver schema is expected to contain ``flight_id``, ``flight_phase``
    plus the engineered sensor columns produced by
    ``bronze_to_silver_etl.py`` (cht_*/egt_*/fqty_*, flight_duration_sec,
    sensor_availability and the kept raw sensors). All numeric sensor values
    are normalised to ``[0, 1]`` in Silver, so the percentile thresholds
    below describe shape, not physical units.
    """
    df = df.copy()

    # fuel_consumed = first_row - last_row per flight, so the per-flight
    # row order must be chronological. Silver materialises seq_idx; when
    # present, sort by it to guarantee the result is independent of how the
    # Parquet was read.
    if "seq_idx" in df.columns:
        df = df.sort_values(["flight_id", "seq_idx"]).reset_index(drop=True)

    thresholds = _compute_thresholds(df)
    logger.info(
        "Anomaly thresholds — cht_spread>%.3f, egt_spread>%.3f, "
        "E1_OilP<%.3f, E1_OilT>%.3f",
        thresholds["cht_spread_max"], thresholds["egt_spread_max"],
        thresholds["oil_press_min"], thresholds["oil_temp_max"],
    )

    df["flag_cht_imbalance"] = df["cht_spread"] > thresholds["cht_spread_max"]
    df["flag_egt_imbalance"] = df["egt_spread"] > thresholds["egt_spread_max"]
    df["flag_low_oil_pressure"] = df["E1_OilP"] < thresholds["oil_press_min"]
    df["flag_high_oil_temp"] = df["E1_OilT"] > thresholds["oil_temp_max"]

    agg = df.groupby("flight_id").agg(
        flight_duration_sec=("flight_duration_sec", "first"),
        sensor_availability=("sensor_availability", "first"),
        avg_rpm=("E1_RPM", "mean"),
        max_rpm=("E1_RPM", "max"),
        std_rpm=("E1_RPM", "std"),
        avg_oil_temp=("E1_OilT", "mean"),
        max_oil_temp=("E1_OilT", "max"),
        avg_oil_pressure=("E1_OilP", "mean"),
        min_oil_pressure=("E1_OilP", "min"),
        avg_cht_mean=("cht_mean", "mean"),
        max_cht_max=("cht_max", "max"),
        avg_cht_spread=("cht_spread", "mean"),
        max_cht_spread=("cht_spread", "max"),
        avg_egt_mean=("egt_mean", "mean"),
        max_egt_max=("egt_max", "max"),
        avg_egt_spread=("egt_spread", "mean"),
        max_egt_spread=("egt_spread", "max"),
        avg_fuel_flow=("E1_FFlow", "mean"),
        max_fuel_imbalance=("fqty_balance", lambda x: x.abs().max()),
        max_altitude=("AltMSL", "max"),
        max_ias=("IAS", "max"),
        max_descent_rate=("VSpd", "min"),
        cht_imbalance_events=("flag_cht_imbalance", "sum"),
        egt_imbalance_events=("flag_egt_imbalance", "sum"),
        low_oil_pressure_events=("flag_low_oil_pressure", "sum"),
        high_oil_temp_events=("flag_high_oil_temp", "sum"),
    ).reset_index()

    # Assumes rows are in chronological order within each flight_id group
    # (the upstream pickle preserves that order).
    fuel_consumed = (
        df.groupby("flight_id")["fqty_total"]
        .agg(lambda x: x.iloc[0] - x.iloc[-1])
        .rename("fuel_consumed")
        .reset_index()
    )
    agg = agg.merge(fuel_consumed, on="flight_id", how="left")

    phase_pct = pd.crosstab(df["flight_id"], df["flight_phase"], normalize="index")
    phase_pct = phase_pct.rename(columns=lambda c: f"pct_{c}").reset_index()
    for col in ["pct_ascent", "pct_cruise", "pct_descent"]:
        if col not in phase_pct.columns:
            phase_pct[col] = 0.0
    agg = agg.merge(
        phase_pct[["flight_id", "pct_ascent", "pct_cruise", "pct_descent"]],
        on="flight_id", how="left",
    )
    agg[["pct_ascent", "pct_cruise", "pct_descent"]] = agg[
        ["pct_ascent", "pct_cruise", "pct_descent"]
    ].fillna(0)

    cruise_alt = (
        df[df["flight_phase"] == "cruise"]
        .groupby("flight_id")["AltMSL"]
        .mean()
        .rename("avg_cruise_altitude")
        .reset_index()
    )
    agg = agg.merge(cruise_alt, on="flight_id", how="left")
    agg["avg_cruise_altitude"] = agg["avg_cruise_altitude"].fillna(0)

    agg["flight_duration_min"] = agg["flight_duration_sec"] / 60
    agg["total_anomaly_count"] = (
        agg["cht_imbalance_events"]
        + agg["egt_imbalance_events"]
        + agg["low_oil_pressure_events"]
        + agg["high_oil_temp_events"]
    )

    # Engine health score — weighted blend, each term in [0, 1], higher = healthier.
    max_anom = agg["total_anomaly_count"].max() or 1
    anom_norm = agg["total_anomaly_count"] / max_anom
    spread_avg = (agg["max_cht_spread"] + agg["max_egt_spread"]) / 2
    agg["engine_health_score"] = 100 * (
        0.30 * (1 - anom_norm)
        + 0.25 * (1 - spread_avg)
        + 0.20 * (1 - agg["max_oil_temp"])
        + 0.15 * agg["min_oil_pressure"]
        + 0.10 * agg["sensor_availability"]
    )
    agg["engine_health_score"] = agg["engine_health_score"].clip(0, 100)

    column_order = [
        "flight_id",
        "flight_duration_sec", "flight_duration_min", "sensor_availability",
        "pct_ascent", "pct_cruise", "pct_descent",
        "avg_rpm", "max_rpm", "std_rpm",
        "avg_oil_temp", "max_oil_temp", "avg_oil_pressure", "min_oil_pressure",
        "avg_cht_mean", "max_cht_max", "avg_cht_spread", "max_cht_spread",
        "avg_egt_mean", "max_egt_max", "avg_egt_spread", "max_egt_spread",
        "fuel_consumed", "avg_fuel_flow", "max_fuel_imbalance",
        "max_altitude", "avg_cruise_altitude", "max_ias", "max_descent_rate",
        "cht_imbalance_events", "egt_imbalance_events",
        "low_oil_pressure_events", "high_oil_temp_events", "total_anomaly_count",
        "engine_health_score",
    ]
    return agg[column_order]


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from loaders import (
        GOLD_BUCKET, GOLD_KEY, LOCAL_GOLD, SILVER_BUCKET,
        _cache_locally, _s3_client, _try_s3_put, load_silver,
    )

    silver_df, src = load_silver()
    logger.info(
        "Silver loaded from %s: %d rows, %d cols, %d flights.",
        src, len(silver_df), silver_df.shape[1], silver_df["flight_id"].nunique(),
    )

    gold_df = compute_gold(silver_df)
    logger.info("Gold built: %d flights, %d KPIs.", len(gold_df), gold_df.shape[1])

    _cache_locally(gold_df, LOCAL_GOLD)
    logger.info("Gold cached locally → %s", LOCAL_GOLD)

    if _try_s3_put(GOLD_BUCKET, GOLD_KEY, gold_df):
        logger.info("Gold uploaded → s3://%s/%s", GOLD_BUCKET, GOLD_KEY)
    else:
        logger.info("S3 upload skipped (LocalStack down). Local cache is the source of truth.")

    # Publish thresholds for the hot-path Lambda. Best-effort: if S3/LocalStack
    # is down the Lambda will simply fall back to DEFAULT_THRESHOLDS.
    _publish_thresholds(_s3_client(), SILVER_BUCKET, THRESHOLDS_KEY, _compute_thresholds(silver_df))

    logger.info(
        "Health score stats: min=%.1f, mean=%.1f, max=%.1f",
        gold_df["engine_health_score"].min(),
        gold_df["engine_health_score"].mean(),
        gold_df["engine_health_score"].max(),
    )
    logger.info(
        "\n%s",
        gold_df[["flight_id", "flight_duration_min", "total_anomaly_count", "engine_health_score"]].head(),
    )


if __name__ == "__main__":
    main()
