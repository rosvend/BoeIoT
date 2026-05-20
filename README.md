# BoeIoT — Plataforma de Analítica IoT para Mantenimiento Predictivo de Aeronaves

**BoeIoT** es una solución de analítica de extremo a extremo que procesa telemetría de sensores de aeronaves para detectar anomalías y predecir fallos de componentes. Implementa una arquitectura Medallion (Bronze → Silver → Gold) sobre AWS, combinando procesamiento batch con dashboards interactivos por capa.

El caso de negocio parte de los incidentes documentados en la línea Boeing 737 MAX: sustituir controles de calidad reactivos por un sistema predictivo basado en datos de sensores IoT, habilitando mantenimiento proactivo, reducción de downtime y trazabilidad auditable ante organismos reguladores (FAA/EASA).

---

## Estructura del repositorio

```
BoeIoT/
├── notebooks/                    # Análisis exploratorio y dashboard interactivo
│   ├── Eda_boeing.ipynb          # EDA: limpieza, distribuciones, correlaciones, detección de anomalías
│   └── dashboard_maintenance.ipynb  # Dashboard operativo por vuelo (Gold layer)
│
├── documentacion/                # Estrategia, procesos, arquitectura y gobernanza
│   ├── estrategia-corporativa.md # Balanced Scorecard, roadmap 12 meses, gestión de riesgos
│   ├── gold_to_dashboard.md      # Esquema Gold: 35 KPIs, fórmula Engine Health Score
│   ├── Boeing_Estrategia_Digital.pptx
│   ├── BoeIoT_Gobierno_de_Datos.docx
│   ├── As_is_boeing.drawio       # Diagrama estado actual de procesos Boeing
│   ├── BPM-Proceso-Manufactura-Boeing.drawio
│   └── DOS - AWS Data architecture.drawio
│
├── dashboard/                    # Dashboards HTML pre-generados (Plotly, autónomos)
│   ├── index.html                # Índice de navegación entre capas
│   ├── bronze.html               # Métricas capa Bronze: cobertura de sensores, nulls
│   ├── silver.html               # Métricas capa Silver: outliers, normalización
│   └── gold.html                 # KPIs por vuelo: health score, anomalías, consumo
│
├── img/                          # Diagramas de arquitectura exportados
│   ├── DOS - AWS Data architecture.jpg
│   ├── As_is_boeing.drawio.png
│   └── Lluvia de ideas.png
│
├── src/
│   ├── scripts/                  # Pipeline ETL completo
│   │   ├── ingest_bronze.py      # Descarga y carga de datos crudos (NGAFID Kaggle)
│   │   ├── bronze_to_silver_etl.py  # Limpieza, normalización, feature engineering
│   │   ├── silver_to_gold_etl.py    # Agregación por vuelo: 35 KPIs
│   │   ├── build_dashboards.py   # Genera dashboard/bronze|silver|gold.html
│   │   └── loaders.py            # Carga con fallback: S3 → caché local → cómputo inline
│   └── infrastructure/           # Infraestructura como código (Terraform + LocalStack)
│       ├── main.tf               # S3 buckets Bronze/Silver/Gold, Glue, Athena, Lambda
│       ├── variables.tf
│       ├── providers.tf
│       └── outputs.tf
│
├── README.md
├── pyproject.toml
└── uv.lock
```

---

## Arquitectura de datos

![Arquitectura AWS](img/DOS%20-%20AWS%20Data%20architecture.jpg)

El proyecto implementa el patrón **Medallion Architecture** sobre un Data Lake en S3:

| Capa | Script | Descripción |
|------|--------|-------------|
| **Bronze (Raw)** | `ingest_bronze.py` | Ingesta datos crudos del dataset NGAFID (50 vuelos, ~23 sensores, frecuencia 1 Hz). Sin transformaciones: preserva estado original. |
| **Silver (Validated)** | `bronze_to_silver_etl.py` | Deduplicación, clipping de valores físicamente imposibles, imputación forward/backward fill para nulos sistémicos, colapso de sensores redundantes (CHT/EGT), normalización MinMax por sensor. |
| **Gold (Enriched)** | `silver_to_gold_etl.py` | Agrega a nivel vuelo: 35 KPIs por `flight_id` incluyendo Engine Health Score (0–100), conteo de anomalías, consumo de combustible, distribución de fases de vuelo, estadísticas de temperatura y presión de aceite. |

El **Engine Health Score** combina: anomalías detectadas (30 %), dispersión entre cilindros CHT (25 %), temperatura de aceite (20 %), presión de aceite (15 %) y disponibilidad de sensores (10 %).

Los umbrales de anomalía se calculan por percentil global (p95 alto / p05 bajo), capturando el 5 % de lecturas más extremas por sensor.

---

## Stack tecnológico

| Categoría | Herramientas |
|-----------|-------------|
| Lenguaje | Python 3.12+ |
| Infraestructura | Terraform, LocalStack (mock AWS local) |
| Procesamiento de datos | Pandas, PyArrow, Scikit-learn |
| Visualización | Plotly, Matplotlib, Seaborn |
| Cloud SDK | Boto3 |
| Gestión de entorno | [uv](https://github.com/astral-sh/uv) |

---

## Cómo ejecutar el proyecto

### Prerrequisitos

- [Docker](https://www.docker.com/) (para LocalStack)
- [LocalStack CLI](https://github.com/localstack/localstack) (`pip install localstack`)
- [Terraform](https://www.terraform.io/) >= 1.0
- Python 3.12+ y [uv](https://github.com/astral-sh/uv)

### 1. Levantar la infraestructura local

Inicia LocalStack para simular los servicios AWS (S3, Glue, Athena, Lambda):

```bash
localstack start
```

Provisiona los recursos con Terraform:

```bash
cd src/infrastructure
terraform init
terraform apply -auto-approve
cd ../..
```

### 2. Configurar el entorno Python

```bash
uv sync
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows
```

### 3. Ejecutar el pipeline ETL

Los clientes S3 en `ingest_bronze.py`, `bronze_to_silver_etl.py` y `loaders.py` se configuran por variables de entorno, de modo que el mismo código funciona contra LocalStack y AWS real. Para ejecuciones locales exporta:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
```

En AWS (p. ej. Glue) deja `AWS_ENDPOINT_URL` sin definir — el SDK usa el rol IAM.

Con la infraestructura levantada y el entorno activo, ejecuta los pipelines en orden:

```bash
# Capa Bronze: descarga y carga datos crudos desde Kaggle (NGAFID dataset)
uv run src/scripts/ingest_bronze.py

# Capa Silver: limpieza, normalización y feature engineering
uv run src/scripts/bronze_to_silver_etl.py

# Capa Gold: agregación por vuelo y cálculo de KPIs
uv run src/scripts/silver_to_gold_etl.py
```

### 4. Ver los dashboards

**Opción A — Dashboards HTML pre-generados** (no requiere infraestructura):

Abre directamente en el navegador:

```
dashboard/index.html
```

**Opción B — Regenerar dashboards desde los datos actuales**:

```bash
uv run src/scripts/build_dashboards.py
```

Esto sobreescribe los archivos en `dashboard/`.

**Opción C — Dashboard interactivo en Jupyter**:

El notebook lee la capa Gold con fallback en tres etapas: S3 → caché local en `data/gold/` → cómputo inline desde Silver. Funciona incluso sin LocalStack activo.

```bash
uv run jupyter notebook notebooks/dashboard_maintenance.ipynb
```

### 5. Explorar el análisis exploratorio

```bash
uv run jupyter notebook notebooks/Eda_boeing.ipynb
```

Contiene: análisis de distribuciones por sensor, detección de outliers, correlaciones, visualización de fases de vuelo y validación de las transformaciones Silver.

---

## Documentación

| Documento | Descripción |
|-----------|-------------|
| [`documentacion/estrategia-corporativa.md`](documentacion/estrategia-corporativa.md) | Balanced Scorecard, análisis de alternativas, roadmap de transformación 12 meses y gestión de riesgos (tecnológico, laboral, regulatorio, cultural) |
| [`documentacion/gold_to_dashboard.md`](documentacion/gold_to_dashboard.md) | Esquema completo de la capa Gold: definición de los 35 KPIs, fórmula del Engine Health Score y guía para extender el dashboard |
| [`documentacion/BoeIoT_Gobierno_de_Datos.docx`](documentacion/BoeIoT_Gobierno_de_Datos.docx) | Marco de gobernanza de datos: roles, políticas de acceso, linaje y ciclo de vida |
| [`documentacion/Boeing_Estrategia_Digital.pptx`](documentacion/Boeing_Estrategia_Digital.pptx) | Presentación ejecutiva de la estrategia digital |
