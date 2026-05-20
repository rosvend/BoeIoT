# Capa Gold + Dashboard de Mantenimiento

Este documento describe la **capa Gold** del data lake BoeIoT, cómo se construye a partir de Silver, y cómo cada KPI alimenta el dashboard de mantenimiento en Jupyter.

---

## 1. Propósito

La capa Gold convierte la telemetría limpia de Silver (registros segundo a segundo de 500 vuelos) en una **tabla resumida por vuelo** orientada al **equipo de mantenimiento**. El objetivo es responder preguntas como:

- ¿Cuáles son los vuelos con peor salud del motor?
- ¿Hay desbalance entre cilindros que indique misfire?
- ¿La presión de aceite cayó por debajo del umbral seguro?
- ¿El consumo de combustible es anormal o asimétrico?
- ¿Qué vuelos requieren inspección física inmediata?

Cada fila representa un vuelo completo (no un instante), lo que reduce millones de filas a cientos y habilita dashboards sin agregación adicional.

---

## 2. Esquema de `flight_summary` (35 columnas)

Tabla almacenada en `s3://dos-boeing-737-max-gold-layer/flight_summary.parquet` (LocalStack) y cacheada en `data/gold/flight_summary.parquet` para corridas offline. El helper `loaders.load_gold()` resuelve en cascada **S3 → caché local → cómputo inline desde Silver**, de modo que el dashboard corre aunque LocalStack esté caído o el ETL no haya sido ejecutado todavía.

> **Nota sobre escalas:** todos los sensores fueron normalizados en Silver al rango `[0, 1]` con `MinMaxScaler`. Por eso métricas como `avg_oil_temp` no están en unidades físicas (°C), sino en fracciones del rango observado en toda la flota. Esto es válido para detección de anomalías y comparación entre vuelos, pero no para reporting de unidades absolutas.

### Identificación y operación (4)

| Columna | Tipo | Descripción | Fórmula |
|---------|------|-------------|---------|
| `flight_id` | string | Clave primaria | Heredada de Bronze |
| `flight_duration_sec` | int | Duración total del vuelo en segundos | `count(rows per flight)` (1 Hz sampling) |
| `flight_duration_min` | float | Duración en minutos (para legibilidad) | `flight_duration_sec / 60` |
| `sensor_availability` | float `[0,1]` | % de lecturas originalmente válidas antes de imputación | Heredada de Silver |

### Distribución de fases (3)

| Columna | Descripción | Fórmula |
|---------|-------------|---------|
| `pct_ascent` | Fracción del vuelo en ascenso | `count(phase=='ascent') / total_rows` |
| `pct_cruise` | Fracción en crucero | `count(phase=='cruise') / total_rows` |
| `pct_descent` | Fracción en descenso | `count(phase=='descent') / total_rows` |

### Motor — RPM (3)

| Columna | Descripción | Agregación |
|---------|-------------|------------|
| `avg_rpm` | Régimen promedio del motor | `mean(E1_RPM)` |
| `max_rpm` | Régimen máximo alcanzado | `max(E1_RPM)` |
| `std_rpm` | Variabilidad del régimen | `std(E1_RPM)` |

### Motor — Lubricación (4)

| Columna | Descripción | Agregación | Interpretación |
|---------|-------------|------------|----------------|
| `avg_oil_temp` | Temperatura promedio de aceite | `mean(E1_OilT)` | Alta → sobrecalentamiento |
| `max_oil_temp` | Temperatura pico | `max(E1_OilT)` | Alta → riesgo crítico |
| `avg_oil_pressure` | Presión promedio | `mean(E1_OilP)` | Baja → fuga / bomba defectuosa |
| `min_oil_pressure` | Presión mínima | `min(E1_OilP)` | Muy baja → falla inminente |

### Motor — Cilindros CHT (4)

| Columna | Descripción | Agregación |
|---------|-------------|------------|
| `avg_cht_mean` | Temperatura media de cilindros, promediada en el vuelo | `mean(cht_mean)` |
| `max_cht_max` | Pico de temperatura entre los 4 cilindros, máximo del vuelo | `max(cht_max)` |
| `avg_cht_spread` | Desbalance promedio entre cilindros | `mean(cht_spread)` |
| `max_cht_spread` | Desbalance máximo (indicador de misfire) | `max(cht_spread)` |

### Motor — Gases de escape EGT (4)

Análogo a CHT, sobre las temperaturas de gases de escape. `egt_spread` alto indica combustión desigual.

### Combustible (3)

| Columna | Descripción | Agregación |
|---------|-------------|------------|
| `fuel_consumed` | Combustible consumido en el vuelo | `fqty_total[primer registro] - fqty_total[último registro]` |
| `avg_fuel_flow` | Flujo promedio | `mean(E1_FFlow)` |
| `max_fuel_imbalance` | Máximo desbalance entre tanques izq/der | `max(abs(fqty_balance))` |

### Aerodinámico (4)

| Columna | Descripción | Agregación |
|---------|-------------|------------|
| `max_altitude` | Altitud máxima alcanzada | `max(AltMSL)` |
| `avg_cruise_altitude` | Altitud media solo en fase de crucero | `mean(AltMSL where phase='cruise')` |
| `max_ias` | Velocidad indicada máxima | `max(IAS)` |
| `max_descent_rate` | Tasa de descenso más agresiva (valor más bajo de VSpd) | `min(VSpd)` |

### Eventos anómalos — conteos (5)

Cada flag es una comparación fila-a-fila contra un percentil global calculado sobre todo Silver.

| Columna | Definición de "evento" |
|---------|------------------------|
| `cht_imbalance_events` | Filas con `cht_spread > p95(cht_spread)` |
| `egt_imbalance_events` | Filas con `egt_spread > p95(egt_spread)` |
| `low_oil_pressure_events` | Filas con `E1_OilP < p05(E1_OilP)` |
| `high_oil_temp_events` | Filas con `E1_OilT > p95(E1_OilT)` |
| `total_anomaly_count` | Suma de los cuatro contadores anteriores |

### Health score compuesto (1)

| Columna | Descripción | Fórmula |
|---------|-------------|---------|
| `engine_health_score` | Score 0–100 de salud del motor (100 = saludable) | Ver sección 5 |

---

## 3. Pipeline Silver → Gold

```text
flight_data_silver.parquet  (millones de filas, 1 fila = 1 segundo de vuelo)
            │
            ▼
[1] Calcular percentiles globales (p95/p05) por sensor crítico
            │
            ▼
[2] Asignar flags booleanos fila a fila para cada tipo de evento anómalo
            │
            ▼
[3] groupby('flight_id') con agregaciones (mean, max, min, std, sum, custom)
            │
            ▼
[4] Calcular columnas derivadas (fuel_consumed, pct_phase, avg_cruise_altitude)
            │
            ▼
[5] Calcular engine_health_score como combinación ponderada
            │
            ▼
flight_summary.parquet  (cientos de filas, 1 fila = 1 vuelo)
```

El script ejecutor es [`src/scripts/silver_to_gold_etl.py`](../src/scripts/silver_to_gold_etl.py).

---

## 4. Mapping Gold → Dashboard

El dashboard ([`src/notebooks/dashboard_maintenance.ipynb`](../src/notebooks/dashboard_maintenance.ipynb)) consume `flight_summary.parquet` directamente. Cada sección depende de un subconjunto específico de columnas:

| # | Sección del dashboard | Columnas Gold consumidas | Pregunta que responde |
|---|----------------------|--------------------------|-----------------------|
| 1 | Resumen de flota | `flight_duration_min`, `engine_health_score`, `total_anomaly_count` | ¿Cuál es el estado general de la flota? |
| 2 | Top 10 vuelos críticos | `flight_id`, `engine_health_score`, `total_anomaly_count`, `max_cht_spread`, `max_egt_spread` | ¿Qué vuelos requieren inspección prioritaria? |
| 3 | Distribución del health score | `engine_health_score` | ¿La mayoría de vuelos están saludables o hay distribución bimodal? |
| 4 | Mapa de calor de anomalías | `flight_id`, `cht_imbalance_events`, `egt_imbalance_events`, `low_oil_pressure_events`, `high_oil_temp_events` | ¿Los problemas se concentran en un subsistema específico? |
| 5 | Presión vs Temperatura de aceite | `avg_oil_temp`, `min_oil_pressure`, `total_anomaly_count`, `engine_health_score` | ¿Hay vuelos en cuadrante peligroso (alta T, baja P)? |
| 6 | Desbalance de cilindros | `max_cht_spread`, `max_egt_spread`, `pct_ascent/cruise/descent` | ¿Los misfires aparecen en alguna fase específica del vuelo? |
| 7 | Consumo de combustible | `flight_id`, `fuel_consumed`, `max_fuel_imbalance` | ¿Qué vuelos tienen consumo excesivo o fuga sospechosa? |
| 8 | Calidad de telemetría | `sensor_availability`, `engine_health_score`, `total_anomaly_count` | ¿Los problemas detectados son reales o artefactos por datos faltantes? |
| 9 | Drill-down temporal de un vuelo | (lee Silver vía `loaders.load_silver`) `AltMSL`, `E1_RPM`, `E1_OilP`, `E1_OilT`, `cht_spread`, `egt_spread` | ¿Cuándo y dónde dentro del vuelo se concentran los eventos anómalos? |

---

## 5. Umbrales y reglas de anomalía

### Detección por percentiles globales

Se usa una estrategia de **percentiles sobre todo el dataset Silver**, en lugar de umbrales físicos fijos. Justificación:

- Los datos están normalizados a `[0, 1]`, por lo que un umbral en unidades físicas no aplica.
- Los percentiles capturan el "5% peor" de cada sensor automáticamente, sin requerir conocimiento previo de los rangos normales.
- Esto da una sensibilidad consistente: aprox. 5% de las filas se marcan como anómalas para cada sensor.

| Sensor | Percentil | Razón |
|--------|-----------|-------|
| `cht_spread` | p95 (alto) | Spread alto = posible misfire / cilindro frío |
| `egt_spread` | p95 (alto) | Spread alto = combustión desigual entre cilindros |
| `E1_OilP` | p05 (bajo) | Presión baja = fuga / bomba / falla inminente |
| `E1_OilT` | p95 (alto) | Temperatura alta = sobrecalentamiento del aceite |

### Fórmula del engine_health_score

```text
score = 100 × [
    0.30 × (1 − anom_norm)         # menos anomalías = mejor
  + 0.25 × (1 − spread_avg)         # menos desbalance de cilindros = mejor
  + 0.20 × (1 − max_oil_temp)       # menor temperatura = mejor
  + 0.15 × min_oil_pressure         # mayor presión mínima = mejor
  + 0.10 × sensor_availability      # mejor calidad de datos = mejor
]
```

Donde:
- `anom_norm = total_anomaly_count / max(total_anomaly_count)` (normalizado al peor caso)
- `spread_avg = (max_cht_spread + max_egt_spread) / 2`
- Cada término aporta a `[0, 1]`; el resultado se clipa a `[0, 100]`

**Pesos:** los conteos de anomalías y el desbalance de cilindros son los indicadores más diagnósticos de problemas mecánicos, por eso pesan 55% combinado. La calidad de datos pesa solo 10% para penalizar telemetría degradada sin dominar el score.

---

## 6. Cómo extender

### Agregar un nuevo KPI a Gold

1. Editar [`src/scripts/silver_to_gold_etl.py`](../src/scripts/silver_to_gold_etl.py):
   - Si es una agregación estándar (`mean/max/min/std/sum`): añadir una línea en el bloque `df.groupby('flight_id').agg(...)`.
   - Si es derivada: calcularla después del `agg` con operaciones sobre las columnas existentes.
2. Añadir la nueva columna a la lista `column_order` para que tenga posición fija en el Parquet.
3. Documentar la columna en este MD en la sección 2.
4. Re-ejecutar: `uv run src/scripts/silver_to_gold_etl.py`.

### Agregar una nueva visualización al dashboard

1. En [`dashboard_maintenance.ipynb`](../src/notebooks/dashboard_maintenance.ipynb), añadir dos celdas al final (markdown con título/descripción + código con la visualización).
2. Documentar la nueva sección en la sección 4 de este MD.

### Cambiar umbrales de anomalía

Editar los percentiles calculados al inicio de `compute_gold()` en [`src/scripts/silver_to_gold_etl.py`](../src/scripts/silver_to_gold_etl.py) (`cht_p95`, `egt_p95`, `oilp_p05`, `oilt_p95`). Más restrictivos (p99/p01) → menos eventos detectados; más laxos (p90/p10) → más eventos detectados.

---

## 7. Limitaciones actuales

- **Sin información meteorológica:** no hay datos de viento, temperatura ambiente externa o condiciones atmosféricas, que afectarían el contexto de las anomalías.
- **Sin streaming / tiempo real:** Gold se construye batch desde Silver. No hay alertas en tiempo real (eso correspondería al "hot path" con Lambda).
- **Sin ML:** los conteos de anomalías son reglas basadas en percentiles. No hay modelos predictivos (clasificación de falla, RUL, etc.).
- **Health score es heurístico:** los pesos del score fueron elegidos a criterio de ingeniería, no calibrados contra datos reales de fallo. Requiere validación con casos históricos cuando estén disponibles.
- **Escalas normalizadas:** las métricas no están en unidades físicas. Para reporting absoluto se necesitaría persistir y revertir el `MinMaxScaler` o mantener una copia no normalizada en Silver.
- **Orden cronológico de filas:** el cálculo de `fuel_consumed` (primer registro menos último) depende de que las filas dentro de cada `flight_id` estén en orden cronológico. `compute_gold()` ordena por `seq_idx` cuando esa columna está presente (caso normal, lo materializa `ingest_bronze.py`); si no lo está, queda como fallback el orden original del DataFrame.
