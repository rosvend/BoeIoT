"""Silver → Gold ETL: aggregates per-second telemetry into per-flight KPIs.

``compute_gold(df)`` is exposed as a pure function so the dashboard notebook
can compute Gold inline when LocalStack is not available.

Run as a script to materialise Gold to the S3 Gold bucket (and a local cache).
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


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

    cht_p95 = df["cht_spread"].quantile(0.95)
    egt_p95 = df["egt_spread"].quantile(0.95)
    oilp_p05 = df["E1_OilP"].quantile(0.05)
    oilt_p95 = df["E1_OilT"].quantile(0.95)
    logger.info(
        "Anomaly thresholds — cht_spread>%.3f, egt_spread>%.3f, "
        "E1_OilP<%.3f, E1_OilT>%.3f",
        cht_p95, egt_p95, oilp_p05, oilt_p95,
    )

    df["flag_cht_imbalance"] = df["cht_spread"] > cht_p95
    df["flag_egt_imbalance"] = df["egt_spread"] > egt_p95
    df["flag_low_oil_pressure"] = df["E1_OilP"] < oilp_p05
    df["flag_high_oil_temp"] = df["E1_OilT"] > oilt_p95

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
    from loaders import GOLD_BUCKET, GOLD_KEY, LOCAL_GOLD, _cache_locally, _try_s3_put, load_silver

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
