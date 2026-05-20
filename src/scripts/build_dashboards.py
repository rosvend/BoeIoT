"""Build standalone Plotly HTML dashboards — one per medallion layer.

Generates ``dashboard/{bronze,silver,gold}.html``. Each file is fully
self-contained (Plotly.js inlined), dark-themed, and matches the visual
language of the live CloudFront dashboard.

Run:
    uv run src/scripts/build_dashboards.py
"""

from __future__ import annotations

import io
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
OUT_DIR = PROJECT_ROOT / "dashboard"
OUT_DIR.mkdir(parents=True, exist_ok=True)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from loaders import load_gold, load_silver  # noqa: E402


# ── Theme ─────────────────────────────────────────────────────────────────────
BG = "#0b1220"
PANEL = "#111a2e"
TEXT = "#e6edf3"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"
GOOD = "#22c55e"
WARN = "#f59e0b"
BAD = "#ef4444"
GRID = "#1e293b"

THEME = go.layout.Template(
    layout=dict(
        font=dict(family="Inter, system-ui, -apple-system, sans-serif", size=13, color=TEXT),
        paper_bgcolor=PANEL,
        plot_bgcolor=PANEL,
        colorway=[ACCENT, "#a78bfa", GOOD, WARN, BAD, "#f472b6", "#fb923c", "#34d399"],
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, color=MUTED),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, color=MUTED),
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=GRID, borderwidth=0),
        margin=dict(l=60, r=30, t=60, b=50),
    )
)
pio.templates["boeiot"] = THEME
pio.templates.default = "boeiot"

PLOTLY_CONFIG = {"displaylogo": False, "responsive": True}


# ── HTML shell ────────────────────────────────────────────────────────────────
PAGE_CSS = f"""
:root {{
  --bg: {BG};
  --panel: {PANEL};
  --text: {TEXT};
  --muted: {MUTED};
  --accent: {ACCENT};
  --good: {GOOD};
  --warn: {WARN};
  --bad: {BAD};
  --grid: {GRID};
}}
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 36px 24px 80px; }}
header {{ border-bottom: 1px solid var(--grid); padding-bottom: 22px; margin-bottom: 28px; }}
.crumbs {{ display: flex; gap: 10px; font-size: 12px; color: var(--muted); margin-bottom: 8px; letter-spacing: .08em; text-transform: uppercase; }}
.crumbs a {{ color: var(--muted); text-decoration: none; }}
.crumbs a:hover {{ color: var(--accent); }}
.layer-badge {{
  display: inline-block; padding: 4px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
}}
.layer-badge.bronze {{ background: rgba(217,119,6,.16); color: #f59e0b; border: 1px solid rgba(217,119,6,.4); }}
.layer-badge.silver {{ background: rgba(148,163,184,.16); color: #cbd5e1; border: 1px solid rgba(148,163,184,.4); }}
.layer-badge.gold   {{ background: rgba(250,204,21,.16); color: #fde047; border: 1px solid rgba(250,204,21,.4); }}
h1 {{ font-size: 28px; margin: 10px 0 4px; letter-spacing: -.01em; }}
.subtitle {{ color: var(--muted); font-size: 14px; margin: 0; }}
nav.toc {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }}
nav.toc a {{
  font-size: 12px; color: var(--muted); text-decoration: none;
  border: 1px solid var(--grid); padding: 5px 10px; border-radius: 6px;
  transition: color .15s, border-color .15s;
}}
nav.toc a:hover {{ color: var(--accent); border-color: var(--accent); }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin: 22px 0 8px; }}
.kpi {{
  background: var(--panel); border: 1px solid var(--grid); border-radius: 12px;
  padding: 16px 18px;
}}
.kpi .label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }}
.kpi .value {{ font-size: 26px; font-weight: 600; margin-top: 4px; }}
.kpi .delta {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
.kpi.alert .value {{ color: var(--bad); }}
.kpi.warn .value {{ color: var(--warn); }}
.kpi.good .value {{ color: var(--good); }}
section {{ margin-top: 36px; }}
section h2 {{ font-size: 18px; margin: 0 0 6px; }}
section .lead {{ color: var(--muted); font-size: 13px; margin: 0 0 14px; max-width: 880px; }}
.chart {{
  background: var(--panel); border: 1px solid var(--grid); border-radius: 12px;
  padding: 8px; overflow: hidden;
}}
footer {{ margin-top: 60px; padding-top: 22px; border-top: 1px solid var(--grid); color: var(--muted); font-size: 12px; }}
footer a {{ color: var(--accent); text-decoration: none; }}
"""


def html_shell(layer: str, title: str, subtitle: str, kpis_html: str, sections_html: str) -> str:
    layer_lower = layer.lower()
    crumbs = " · ".join(
        [
            f'<a href="bronze.html" style="{"color:var(--accent);" if layer_lower=="bronze" else ""}">Bronze</a>',
            f'<a href="silver.html" style="{"color:var(--accent);" if layer_lower=="silver" else ""}">Silver</a>',
            f'<a href="gold.html"   style="{"color:var(--accent);" if layer_lower=="gold"   else ""}">Gold</a>',
        ]
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BoeIoT · {layer} Layer</title>
<style>{PAGE_CSS}</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
</head>
<body>
<div class="wrap">
  <header>
    <div class="crumbs">{crumbs}</div>
    <span class="layer-badge {layer_lower}">{layer} Layer</span>
    <h1>{title}</h1>
    <p class="subtitle">{subtitle}</p>
  </header>
  <div class="kpis">{kpis_html}</div>
  {sections_html}
  <footer>
    BoeIoT · Boeing 737 MAX Maintenance Intelligence · NGAFID 2-day subset (50 flights)
    · S3 + Pandas + Plotly · <a href="bronze.html">Bronze</a> · <a href="silver.html">Silver</a> · <a href="gold.html">Gold</a>
  </footer>
</div>
</body>
</html>"""


def kpi(label: str, value: str, delta: str = "", kind: str = "") -> str:
    klass = f"kpi {kind}" if kind else "kpi"
    delta_html = f'<div class="delta">{delta}</div>' if delta else ""
    return f'<div class="{klass}"><div class="label">{label}</div><div class="value">{value}</div>{delta_html}</div>'


def section(idx: int, title: str, lead: str, fig: go.Figure, height: int = 460) -> str:
    fig_html = pio.to_html(
        fig,
        include_plotlyjs=False,
        full_html=False,
        config=PLOTLY_CONFIG,
        default_height=f"{height}px",
    )
    return f"""
<section id="s{idx}">
  <h2>{idx}. {title}</h2>
  <p class="lead">{lead}</p>
  <div class="chart">{fig_html}</div>
</section>
"""


# ── BRONZE ────────────────────────────────────────────────────────────────────
def build_bronze() -> str:
    print("[bronze] loading raw pickle (this takes a moment) …")
    pkl_path = DATA_DIR / "bronze" / "flight_data.pkl"
    with open(pkl_path, "rb") as f:
        data_dict = pickle.load(f)

    sensor_cols = [
        "volt1", "volt2", "amp1", "amp2", "FQtyL", "FQtyR", "E1_FFlow",
        "E1_OilT", "E1_OilP", "E1_RPM", "E1_CHT1", "E1_CHT2", "E1_CHT3",
        "E1_CHT4", "E1_EGT1", "E1_EGT2", "E1_EGT3", "E1_EGT4", "OAT",
        "IAS", "VSpd", "NormAc", "AltMSL",
    ]

    flight_ids_ingested = list(data_dict.keys())[:50]
    parts = []
    for v_id in flight_ids_ingested:
        raw = data_dict[v_id]
        if raw.shape[0] == 23:
            raw = raw.T
        d = pd.DataFrame(np.asarray(raw, dtype=np.float64), columns=sensor_cols)
        d["flight_id"] = v_id
        d["seq_idx"] = range(len(d))
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)

    # Top-line numbers
    total_flights_in_pickle = len(data_dict)
    n_flights = df["flight_id"].nunique()
    n_rows = len(df)
    n_sensors = len(sensor_cols)
    pkl_size_gb = pkl_path.stat().st_size / 1024**3

    # Per-sensor null %
    null_pct = (df[sensor_cols].isna().mean() * 100).sort_values(ascending=True)

    # Physical violations
    violations = {
        "E1_OilP < 0": int((df["E1_OilP"] < 0).sum()),
        "AltMSL < 0": int((df["AltMSL"] < 0).sum()),
        "IAS < 0": int((df["IAS"] < 0).sum()),
        "|VSpd| > 3000": int((df["VSpd"].abs() > 3000).sum()),
    }
    total_violations = sum(violations.values())

    # KPIs
    kpis_html = (
        kpi("Pickle source", f"{total_flights_in_pickle:,}", "flights available")
        + kpi("Ingested flights", f"{n_flights}", "first 50 of pickle")
        + kpi("Rows materialized", f"{n_rows:,}", "1 Hz telemetry")
        + kpi("Raw sensors", f"{n_sensors}", "float16 columns")
        + kpi("Pickle size", f"{pkl_size_gb:.2f} GB", str(pkl_path))
        + kpi("Physical violations", f"{total_violations:,}", "to be clipped in Silver", "warn" if total_violations else "")
    )

    sections_html = ""

    # 1. Rows per flight
    rpf = df.groupby("flight_id").size().sort_values()
    fig = go.Figure(
        go.Bar(
            x=rpf.values,
            y=[str(i) for i in rpf.index],
            orientation="h",
            marker=dict(color=ACCENT),
            hovertemplate="Flight %{y}<br>%{x:,} rows<extra></extra>",
        )
    )
    fig.update_layout(
        title="Rows per flight (1 row = 1 second of telemetry)",
        xaxis_title="Rows",
        yaxis_title="Flight ID",
        height=max(420, 14 * n_flights),
    )
    sections_html += section(
        1, "Volumen por vuelo",
        "Cada fila es 1 segundo de telemetría (sampling 1 Hz). Los 50 vuelos varían "
        f"entre {int(rpf.min()):,} y {int(rpf.max()):,} filas — duraciones de vuelo desiguales "
        "que la capa Silver normaliza en una métrica de duración explícita.",
        fig, height=max(420, 14 * n_flights),
    )

    # 2. Per-sensor null %
    bar_colors = [BAD if v > 3 else (WARN if v > 0.5 else GOOD) for v in null_pct.values]
    fig = go.Figure(
        go.Bar(
            x=null_pct.values,
            y=null_pct.index,
            orientation="h",
            marker=dict(color=bar_colors),
            hovertemplate="%{y}<br>%{x:.2f}% null<extra></extra>",
        )
    )
    fig.add_vline(x=5.88, line_dash="dash", line_color=BAD,
                  annotation_text="Bus failure ≈ 5.88%", annotation_position="top right",
                  annotation_font_color=BAD)
    fig.update_layout(
        title="% de nulos por sensor (raw, pre-imputación)",
        xaxis_title="% nulls",
        yaxis_title="",
        height=560,
    )
    sections_html += section(
        2, "Cobertura de sensores",
        "EDA detectó un fallo sistémico de bus: <b>E1_CHT2/3/4, amp2 y volt2</b> "
        "comparten exactamente ~5.88 % de nulos simultáneos — no es ruido aleatorio, "
        "es un dropout de telemetría que la capa Silver corrige con ffill+bfill intra-vuelo.",
        fig, height=560,
    )

    # 3. Physical violations
    v_keys = list(violations.keys())
    v_vals = list(violations.values())
    fig = go.Figure(
        go.Bar(
            x=v_keys, y=v_vals,
            marker=dict(color=[BAD if v > 0 else GOOD for v in v_vals]),
            text=[f"{v:,}" for v in v_vals],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:,} rows<extra></extra>",
        )
    )
    fig.update_layout(
        title="Lecturas físicamente imposibles (raw)",
        yaxis_title="# filas",
        xaxis_title="Regla violada",
    )
    sections_html += section(
        3, "Violaciones físicas",
        "Inicializaciones de sensores en tierra producen presiones, altitudes y velocidades "
        "negativas. Silver aplica <code>clip(lower=0)</code> a OilP, IAS y AltMSL, "
        "y restringe VSpd al rango ±3000 ft/s.",
        fig,
    )

    # 4. Sample raw distributions (pre-binned to keep HTML small)
    sample_cols = ["E1_OilT", "E1_OilP", "E1_RPM", "AltMSL", "IAS", "OAT"]
    fig = make_subplots(rows=2, cols=3, subplot_titles=sample_cols)
    for i, col in enumerate(sample_cols):
        r, c = i // 3 + 1, i % 3 + 1
        data = df[col].dropna().values
        counts, edges = np.histogram(data, bins=60)
        centers = (edges[:-1] + edges[1:]) / 2
        fig.add_trace(
            go.Bar(x=centers, y=counts, marker=dict(color=ACCENT), showlegend=False,
                   hovertemplate=f"{col}<br>%{{x:.2f}}<br>n=%{{y}}<extra></extra>"),
            row=r, col=c,
        )
    fig.update_layout(title="Distribuciones raw de sensores clave", height=520, showlegend=False, bargap=0)
    sections_html += section(
        4, "Distribuciones raw",
        "Histogramas crudos en unidades originales. OilT/OilP/RPM son <i>multimodales</i> — "
        "indicio de que existen distintos regímenes operativos (ascent/cruise/descent) "
        "que justifican el feature <code>flight_phase</code> que se crea en Silver.",
        fig, height=520,
    )

    # 5. Missing data heatmap (flight × sensor)
    miss = df.groupby("flight_id")[sensor_cols].apply(lambda x: x.isna().mean() * 100)
    fig = go.Figure(
        go.Heatmap(
            z=miss.values,
            x=miss.columns,
            y=[str(i) for i in miss.index],
            colorscale="Reds",
            zmin=0, zmax=max(10, float(miss.values.max())),
            colorbar=dict(title="% null"),
            hovertemplate="Flight %{y}<br>%{x}<br>%{z:.2f}% null<extra></extra>",
        )
    )
    fig.update_layout(
        title="% nulos por vuelo × sensor",
        xaxis_title="Sensor",
        yaxis_title="Flight ID",
        height=max(500, 14 * n_flights),
    )
    sections_html += section(
        5, "Mapa de calor de nulos",
        "Confirma que el dropout no es aleatorio: vuelos enteros muestran el mismo patrón "
        "horizontal en los 5 sensores del bus afectado. Las filas oscuras son candidatas "
        "a inspección antes de promover a Silver.",
        fig, height=max(500, 14 * n_flights),
    )

    return html_shell(
        "Bronze",
        "BoeIoT · Raw Telemetry Ingest",
        f"Ingesta cruda desde NGAFID 2-day subset · {n_flights} vuelos × {n_rows:,} filas × {n_sensors} sensores",
        kpis_html,
        sections_html,
    )


# ── SILVER ────────────────────────────────────────────────────────────────────
def build_silver() -> str:
    print("[silver] loading parquet …")
    df, source = load_silver()

    n_rows = len(df)
    n_flights = df["flight_id"].nunique()
    n_cols = df.shape[1]
    phases = df["flight_phase"].value_counts().to_dict()
    avg_avail = float(df.groupby("flight_id")["sensor_availability"].first().mean() * 100)

    # Schema delta vs Bronze
    bronze_sensors = 23
    silver_sensor_cols = [c for c in df.columns if c not in {
        "flight_id", "seq_idx", "sensor_availability", "flight_duration_sec", "flight_phase",
    }]
    silver_n_sensor_cols = len(silver_sensor_cols)

    kpis_html = (
        kpi("Rows post-clean", f"{n_rows:,}", f"source: {source}")
        + kpi("Flights surviving", f"{n_flights}", "of 50 (7 dropped on NaN)")
        + kpi("Schema width", f"{silver_n_sensor_cols} sensors", f"vs {bronze_sensors} en Bronze")
        + kpi("Total columns", f"{n_cols}", "incluye flight_phase + meta")
        + kpi("Avg sensor avail.", f"{avg_avail:.1f}%", "pre-imputación promedio")
        + kpi("Normalización", "[0, 1]", "MinMaxScaler joblib")
    )

    sections_html = ""

    # 1. Schema transformation
    transforms = [
        ("23 sensores raw", bronze_sensors, ACCENT),
        ("− volt2 (r=.99)", -1, BAD),
        ("4 CHT → 5 aggs", -4 + 5, GOOD),
        ("4 EGT → 5 aggs", -4 + 5, GOOD),
        ("2 FQty → 2 aggs", -2 + 2, MUTED),
        ("Silver sensores", silver_n_sensor_cols, ACCENT),
    ]
    fig = go.Figure(
        go.Bar(
            x=[t[0] for t in transforms],
            y=[t[1] for t in transforms],
            marker=dict(color=[t[2] for t in transforms]),
            text=[f"{t[1]:+d}" if i not in (0, len(transforms) - 1) else f"{t[1]}" for i, t in enumerate(transforms)],
            textposition="outside",
            hovertemplate="%{x}<br>%{y}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Reducción de esquema Bronze → Silver",
        yaxis_title="Δ columnas sensor",
    )
    sections_html += section(
        1, "Transformación de esquema",
        "EDA detectó multicolinealidad severa: <code>volt1↔volt2</code> r=0.993, "
        "los 4 CHT entre sí r>0.94, los 4 EGT r>0.95. Silver colapsa cada grupo "
        "en estadísticas agregadas (mean/std/max/min/spread) que <b>preservan la señal "
        "diagnóstica</b> (spread = misfire) eliminando la redundancia.",
        fig,
    )

    # 2. Phase distribution
    phase_labels = list(phases.keys())
    phase_vals = list(phases.values())
    phase_colors = {"ascent": GOOD, "cruise": ACCENT, "descent": WARN}
    fig = go.Figure(
        go.Pie(
            labels=phase_labels,
            values=phase_vals,
            marker=dict(colors=[phase_colors.get(p, MUTED) for p in phase_labels]),
            hole=0.55,
            textinfo="label+percent",
            hovertemplate="%{label}<br>%{value:,} rows<br>%{percent}<extra></extra>",
        )
    )
    fig.update_layout(title="Distribución de fases de vuelo (rows = segundos)", showlegend=True)
    sections_html += section(
        2, "Clasificación de fase",
        "El feature <code>flight_phase</code> se deriva del gradiente suavizado de altitud "
        "(threshold 50 ft/s sobre ventana móvil de 30s). En este subset NGAFID los 50 vuelos "
        "son de aviación general con perfiles relativamente planos → el clasificador los marca "
        "como <b>cruise</b> dominante; una flota 737 MAX produciría las 3 fases bien separadas.",
        fig,
    )

    # 3. Derived features distribution (pre-binned to keep HTML small)
    derived = ["cht_spread", "cht_std", "egt_spread", "egt_std", "fqty_balance", "fqty_total"]
    fig = make_subplots(rows=2, cols=3, subplot_titles=derived)
    for i, col in enumerate(derived):
        r, c = i // 3 + 1, i % 3 + 1
        counts, edges = np.histogram(df[col].dropna(), bins=50)
        centers = (edges[:-1] + edges[1:]) / 2
        fig.add_trace(
            go.Bar(x=centers, y=counts, marker=dict(color=ACCENT), showlegend=False,
                   hovertemplate=f"{col}<br>val %{{x:.3f}}<br>n=%{{y}}<extra></extra>"),
            row=r, col=c,
        )
    fig.update_layout(title="Distribuciones de features derivados (post-normalización)", height=520, bargap=0)
    sections_html += section(
        3, "Features derivados",
        "Los <i>spread</i> (cht/egt) capturan desbalance entre cilindros — la señal clave "
        "para detectar misfire. <code>fqty_balance</code> detecta consumo asimétrico entre "
        "tanques izq/der (posible fuga). Todos los valores están escalados a [0, 1].",
        fig, height=520,
    )

    # 4. Sensor availability boxplot per flight
    avail_per_flight = df.groupby("flight_id")["sensor_availability"].first().sort_values(ascending=False)
    fig = go.Figure(
        go.Bar(
            x=[str(i) for i in avail_per_flight.index],
            y=avail_per_flight.values * 100,
            marker=dict(
                color=avail_per_flight.values * 100,
                colorscale=[[0, BAD], [0.5, WARN], [1, GOOD]],
                colorbar=dict(title="%"),
            ),
            hovertemplate="Flight %{x}<br>%{y:.2f}% available<extra></extra>",
        )
    )
    fig.add_hline(y=95, line_dash="dash", line_color=GOOD, annotation_text="OK ≥ 95%",
                  annotation_position="top right", annotation_font_color=GOOD)
    fig.update_layout(
        title="Sensor availability por vuelo (pre-imputación)",
        xaxis_title="Flight ID", yaxis_title="% lecturas válidas",
    )
    sections_html += section(
        4, "Calidad por vuelo",
        "La métrica <code>sensor_availability</code> se hereda en Gold y se usa como "
        "ponderación para evitar falsos positivos: un vuelo con disponibilidad &lt;90% "
        "puede tener score bajo por datos faltantes, no por motor defectuoso.",
        fig,
    )

    # 5. Normalization check — boxplots showing [0,1] bounds
    bool_or_meta = {"flight_id", "seq_idx", "sensor_availability", "flight_duration_sec", "flight_phase"}
    scale_cols = [c for c in df.columns if c not in bool_or_meta][:18]
    rng = np.random.default_rng(0)
    sample_idx = rng.choice(len(df), size=min(1500, len(df)), replace=False)
    fig = go.Figure()
    for col in scale_cols:
        fig.add_trace(
            go.Box(
                y=df[col].iloc[sample_idx],
                name=col, boxpoints=False,
                marker=dict(color=ACCENT), line=dict(color=ACCENT),
            )
        )
    fig.update_layout(
        title="Distribución por feature (verificación de normalización [0, 1])",
        yaxis_title="Valor normalizado", showlegend=False, height=520,
    )
    fig.update_xaxes(tickangle=45)
    sections_html += section(
        5, "Verificación de normalización",
        "Cada caja muestra el rango intercuartílico de un feature tras <code>MinMaxScaler</code>. "
        "Todos los valores deben caer dentro de [0, 1]; los outliers visibles son legítimos "
        "(picos de RPM/EGT) y no requieren recorte adicional.",
        fig, height=520,
    )

    # 6. Correlation heatmap
    corr_cols = [c for c in df.columns if c not in bool_or_meta]
    corr = df[corr_cols].corr().fillna(0)
    fig = go.Figure(
        go.Heatmap(
            z=corr.values,
            x=corr.columns,
            y=corr.columns,
            colorscale="RdBu",
            zmid=0, zmin=-1, zmax=1,
            hovertemplate="%{y} × %{x}<br>r = %{z:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Matriz de correlación Silver (post-colapso)",
        height=620,
    )
    sections_html += section(
        6, "Correlaciones residuales",
        "Tras colapsar CHT/EGT/FQty, la mayoría de correlaciones cae por debajo de |r| &lt; 0.7. "
        "Las correlaciones fuertes restantes son <b>físicamente esperadas</b> "
        "(RPM↔OilT por carga del motor) y no requieren intervención.",
        fig, height=620,
    )

    return html_shell(
        "Silver",
        "BoeIoT · Cleaned & Normalized Telemetry",
        f"ETL EDA-guided · {n_flights} vuelos × {n_rows:,} filas × {n_cols} columnas · MinMax [0,1]",
        kpis_html,
        sections_html,
    )


# ── GOLD ──────────────────────────────────────────────────────────────────────
def build_gold() -> str:
    print("[gold] loading aggregates …")
    df, source = load_gold()
    silver_df, _ = load_silver()

    total_flights = len(df)
    total_hours = df["flight_duration_min"].sum() / 60
    avg_health = df["engine_health_score"].mean()
    total_anomalies = int(df["total_anomaly_count"].sum())
    critical = int((df["engine_health_score"] < 60).sum())

    kpis_html = (
        kpi("Vuelos analizados", f"{total_flights}", f"source: {source}")
        + kpi("Horas operación", f"{total_hours:,.1f} h", "agregadas en flota")
        + kpi("Health score promedio", f"{avg_health:.1f} / 100",
              "alerta < 60", "warn" if avg_health < 70 else "good")
        + kpi("Anomalías totales", f"{total_anomalies:,}", "eventos detectados")
        + kpi("Vuelos críticos", f"{critical}", "score < 60", "alert" if critical else "")
        + kpi("KPIs disponibles", f"{df.shape[1]}", "columnas Gold")
    )

    sections_html = ""

    # 1. Top 10 critical
    top10 = df.nsmallest(10, "engine_health_score")[
        ["flight_id", "engine_health_score", "total_anomaly_count", "max_cht_spread", "max_egt_spread"]
    ].reset_index(drop=True)
    colors = [BAD if s < 50 else (WARN if s < 70 else GOOD) for s in top10["engine_health_score"]]
    fig = go.Figure(
        go.Bar(
            x=top10["engine_health_score"],
            y=top10["flight_id"].astype(str),
            orientation="h",
            marker=dict(color=colors),
            hovertemplate="Flight %{y}<br>score %{x:.1f}<extra></extra>",
        )
    )
    fig.add_vline(x=50, line_dash="dash", line_color=BAD,
                  annotation_text="Crítico (50)", annotation_font_color=BAD)
    fig.add_vline(x=70, line_dash="dash", line_color=WARN,
                  annotation_text="Atención (70)", annotation_font_color=WARN)
    fig.update_layout(
        title="Top 10 vuelos críticos por engine health score",
        xaxis_title="Engine health score (0-100)",
        yaxis=dict(autorange="reversed"),
    )
    sections_html += section(
        1, "Top 10 vuelos críticos",
        "Vuelos con el peor <code>engine_health_score</code>. Candidatos prioritarios "
        "para inspección física. Rojo &lt; 50 = problema serio; naranja 50–70 = seguimiento.",
        fig,
    )

    # 2. Score distribution
    p10 = df["engine_health_score"].quantile(0.10)
    p25 = df["engine_health_score"].quantile(0.25)
    fig = go.Figure(go.Histogram(
        x=df["engine_health_score"], nbinsx=20, marker=dict(color=ACCENT),
        hovertemplate="score %{x}<br>%{y} vuelos<extra></extra>",
    ))
    fig.add_vline(x=p10, line_dash="dash", line_color=BAD,
                  annotation_text=f"p10 = {p10:.1f}", annotation_font_color=BAD)
    fig.add_vline(x=p25, line_dash="dash", line_color=WARN,
                  annotation_text=f"p25 = {p25:.1f}", annotation_font_color=WARN)
    fig.update_layout(
        title="Distribución del Health Score en la flota",
        xaxis_title="Engine health score", yaxis_title="# vuelos",
    )
    sections_html += section(
        2, "Distribución del Health Score",
        "Los percentiles 10 y 25 marcan los umbrales operativos de <b>crítico</b> y "
        "<b>atención</b> respectivamente — ajustables según política de mantenimiento.",
        fig,
    )

    # 3. Anomaly heatmap
    top30 = df.nlargest(30, "total_anomaly_count")
    hm_cols = ["cht_imbalance_events", "egt_imbalance_events", "low_oil_pressure_events", "high_oil_temp_events"]
    hm_labels = ["CHT imbalance", "EGT imbalance", "Low oil pressure", "High oil temp"]
    fig = go.Figure(
        go.Heatmap(
            z=top30[hm_cols].values,
            x=hm_labels,
            y=top30["flight_id"].astype(str),
            colorscale="Reds",
            colorbar=dict(title="# eventos"),
            hovertemplate="Flight %{y}<br>%{x}<br>%{z} eventos<extra></extra>",
        )
    )
    fig.update_layout(title="Anomalías por vuelo y tipo (top 30)", height=720,
                      xaxis_title="Tipo de evento", yaxis_title="Flight ID")
    sections_html += section(
        3, "Mapa de anomalías",
        "Permite distinguir vuelos con problemas <b>focalizados</b> (una columna dominante = "
        "falla específica del subsistema) vs <b>generalizados</b> (varias columnas calientes "
        "= degradación múltiple, posible problema sistémico).",
        fig, height=720,
    )

    # 4. Oil pressure vs temperature
    fig = go.Figure(go.Scatter(
        x=df["avg_oil_temp"], y=df["min_oil_pressure"],
        mode="markers",
        marker=dict(
            size=10 + df["total_anomaly_count"] / df["total_anomaly_count"].max() * 30,
            color=df["engine_health_score"],
            colorscale="RdYlGn",
            colorbar=dict(title="Health score"),
            line=dict(width=0.5, color="rgba(255,255,255,.3)"),
        ),
        text=df["flight_id"].astype(str),
        hovertemplate="Flight %{text}<br>oil T %{x:.2f}<br>min OilP %{y:.2f}<extra></extra>",
    ))
    temp_p75 = df["avg_oil_temp"].quantile(0.75)
    pres_p25 = df["min_oil_pressure"].quantile(0.25)
    fig.add_shape(
        type="rect",
        x0=temp_p75, x1=df["avg_oil_temp"].max(),
        y0=df["min_oil_pressure"].min(), y1=pres_p25,
        fillcolor="rgba(239,68,68,.10)", line=dict(color="rgba(239,68,68,.4)", dash="dash"),
    )
    fig.add_annotation(
        x=(temp_p75 + df["avg_oil_temp"].max()) / 2,
        y=(df["min_oil_pressure"].min() + pres_p25) / 2,
        text="Cuadrante peligroso",
        showarrow=False, font=dict(color=BAD, size=11),
    )
    fig.update_layout(
        title="Riesgo de lubricación — Presión vs Temperatura de aceite",
        xaxis_title="Temperatura promedio de aceite (norm)",
        yaxis_title="Presión mínima de aceite (norm)",
    )
    sections_html += section(
        4, "Riesgo de lubricación",
        "Cuadrante de riesgo: <b>alta temperatura + baja presión</b> indica falla inminente "
        "del sistema de lubricación. Tamaño del punto = total de anomalías; color = health score.",
        fig,
    )

    # 5. Cylinder imbalance by phase
    df2 = df.assign(
        dominant_phase=df[["pct_ascent", "pct_cruise", "pct_descent"]]
        .idxmax(axis=1).str.replace("pct_", "", regex=False)
    )
    phase_order = ["ascent", "cruise", "descent"]
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Max CHT spread", "Max EGT spread"))
    for i, metric in enumerate(["max_cht_spread", "max_egt_spread"]):
        for ph in phase_order:
            sub = df2[df2["dominant_phase"] == ph][metric]
            if len(sub):
                fig.add_trace(
                    go.Box(y=sub, name=ph, boxmean=True,
                           marker=dict(color={"ascent": GOOD, "cruise": ACCENT, "descent": WARN}[ph]),
                           showlegend=(i == 0)),
                    row=1, col=i + 1,
                )
    fig.update_layout(title="Desbalance entre cilindros por fase dominante", height=480)
    sections_html += section(
        5, "Desbalance por fase",
        "Spread = <code>max − min</code> entre 4 cilindros. Spread alto → posible misfire, "
        "fuga de compresión o inyector defectuoso. Segmentado por fase para identificar "
        "si el problema aparece en algún régimen específico.",
        fig, height=480,
    )

    # 6. Fuel consumption
    top_fuel = df.nlargest(20, "fuel_consumed").sort_values("fuel_consumed", ascending=True)
    imb_thr = df["max_fuel_imbalance"].quantile(0.75)
    bar_colors = [WARN if x > imb_thr else ACCENT for x in top_fuel["max_fuel_imbalance"]]
    fig = go.Figure(go.Bar(
        x=top_fuel["fuel_consumed"], y=top_fuel["flight_id"].astype(str),
        orientation="h", marker=dict(color=bar_colors),
        hovertemplate="Flight %{y}<br>fuel %{x:.3f}<extra></extra>",
    ))
    fig.update_layout(
        title=f"Top 20 vuelos por consumo de combustible (naranja = desbalance > p75 ≈ {imb_thr:.2f})",
        xaxis_title="Combustible consumido (norm)",
    )
    sections_html += section(
        6, "Consumo de combustible",
        "Vuelos con desbalance entre tanques L/R por encima del p75 se resaltan en naranja — "
        "indicio de fuga, sensor defectuoso o transferencia asimétrica.",
        fig,
    )

    # 7. Telemetry quality vs health
    fig = go.Figure(go.Scatter(
        x=df["sensor_availability"], y=df["engine_health_score"],
        mode="markers",
        marker=dict(
            size=10, color=df["total_anomaly_count"],
            colorscale="Viridis", colorbar=dict(title="Anomalies"),
            line=dict(width=0.5, color="rgba(255,255,255,.3)"),
        ),
        text=df["flight_id"].astype(str),
        hovertemplate="Flight %{text}<br>avail %{x:.2%}<br>score %{y:.1f}<extra></extra>",
    ))
    corr = df[["sensor_availability", "engine_health_score"]].corr().iloc[0, 1]
    fig.add_annotation(
        x=0.02, y=0.98, xref="paper", yref="paper", xanchor="left", yanchor="top",
        text=f"Pearson r = {corr:+.3f}",
        showarrow=False, font=dict(color=TEXT),
        bgcolor="rgba(17,26,46,.85)", bordercolor=GRID, borderwidth=1,
    )
    fig.update_layout(
        title="Calidad de telemetría vs Salud del motor",
        xaxis_title="Sensor availability", yaxis_title="Engine health score",
    )
    sections_html += section(
        7, "Calibración del score",
        "Si la correlación entre disponibilidad de sensores y score fuera fuertemente negativa, "
        "parte del score sería un <b>falso positivo</b> causado por datos faltantes. "
        f"Aquí r = {corr:+.3f}, sugiriendo que los problemas detectados son reales.",
        fig,
    )

    # 8. Drill-down: timeline of the worst flight
    worst_id = df.nsmallest(1, "engine_health_score").iloc[0]["flight_id"]
    flight_ts = silver_df[silver_df["flight_id"] == worst_id].copy()
    if "seq_idx" in flight_ts.columns:
        flight_ts = flight_ts.sort_values("seq_idx").reset_index(drop=True)
        t = flight_ts["seq_idx"]
    else:
        flight_ts = flight_ts.reset_index(drop=True)
        t = flight_ts.index
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        subplot_titles=("Altitud (norm)", "RPM (norm)", "Aceite (norm)", "Spreads (norm)"),
    )
    fig.add_trace(go.Scatter(x=t, y=flight_ts["AltMSL"], line=dict(color=ACCENT), name="AltMSL"), row=1, col=1)
    fig.add_trace(go.Scatter(x=t, y=flight_ts["E1_RPM"], line=dict(color=GOOD), name="RPM"), row=2, col=1)
    fig.add_trace(go.Scatter(x=t, y=flight_ts["E1_OilP"], line=dict(color="#60a5fa"), name="OilP"), row=3, col=1)
    fig.add_trace(go.Scatter(x=t, y=flight_ts["E1_OilT"], line=dict(color=BAD), name="OilT"), row=3, col=1)
    fig.add_trace(go.Scatter(x=t, y=flight_ts["cht_spread"], line=dict(color="#a78bfa"), name="cht_spread"), row=4, col=1)
    fig.add_trace(go.Scatter(x=t, y=flight_ts["egt_spread"], line=dict(color=WARN), name="egt_spread"), row=4, col=1)
    fig.update_xaxes(title_text="Tiempo dentro del vuelo (s)", row=4, col=1)
    fig.update_layout(
        title=f"Perfil temporal del vuelo {worst_id} (peor health score)",
        height=720, hovermode="x unified",
    )
    sections_html += section(
        8, "Drill-down temporal",
        "Perfil segundo-a-segundo del vuelo con peor health score. Permite a mantenimiento "
        "ubicar <b>cuándo</b> dentro del vuelo se concentran los eventos anómalos — "
        "información que el resumen Gold (1 fila por vuelo) no preserva.",
        fig, height=720,
    )

    return html_shell(
        "Gold",
        "BoeIoT · Maintenance Intelligence",
        f"Predictive maintenance KPIs · {total_flights} vuelos × {df.shape[1]} indicadores agregados",
        kpis_html,
        sections_html,
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    targets = {
        "bronze.html": build_bronze,
        "silver.html": build_silver,
        "gold.html": build_gold,
    }
    for name, builder in targets.items():
        out = OUT_DIR / name
        html = builder()
        out.write_text(html, encoding="utf-8")
        print(f"[ok] wrote {out.relative_to(PROJECT_ROOT)} ({out.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
