# inspector-synth

Synthetic ThoughtSpot → Strategy (Mosaic) migration packages with known, injected defects, plus a
seeded warehouse in which every defect produces a visible numeric difference.

## Quick start

```bash
uv sync                                 # from the repo root (uv workspace)
uv run inspector-synth warehouse        # warehouse/data/*.csv + Postgres init scripts
docker compose up -d warehouse          # Postgres on :5439, seeded on first start
uv run inspector-synth generate         # packages/ + ground_truth/
uv run inspector-synth verify           # runs every witness query pair against the warehouse
uv run pytest synthetic
```

Everything is deterministic (fixed seed): regenerating produces byte-identical files.

## Layout

| Path | What | Inspector reads it? |
|---|---|---|
| `packages/Sxx/` | One migration package: ThoughtSpot TML + SQL, Strategy model + dashboards + SQL view, optional `mapping.yaml` | **Yes** |
| `ground_truth/Sxx/expected_findings.yaml` | Expected verdict, findings (check id, severity, root cause, symptoms, acceptable alternatives), `must_pass` fields | No (eval only) |
| `ground_truth/Sxx/witnesses/` | Query pairs (ThoughtSpot semantics vs Strategy semantics) that must differ or match | No (eval only) |
| `warehouse/` | CSVs, Postgres init scripts (`01_schema`, `02_load`, `03_readonly_role`), Redshift DDL | data_agent |

Ground truth is kept outside the packages so nothing Inspector (or its LLM) reads can give away the
answer. A test enforces this.

## Domain

Retail on `analytics.retail`: `fact_orders` (24k), `fact_order_lines` (72k), `fact_returns` (1.6k),
`dim_customer`, `dim_store`, `dim_product`, `dim_date`.

Model filters in lookup-style Strategy models are built the way developers do it: the fact table is a one-table
Freeform SQL with the `WHERE` clause.

Traps: ~3% orphan customers, ~2% order lines with orphan products, NULL discounts and shipping,
zero-revenue `Replacement` orders, UTC timestamps near midnight with local `order_date`, Monday and
Sunday week starts, and a fiscal year starting in April.

ThoughtSpot models: **Sales Orders** (Worksheet TML), **Order Lines** (Model TML), **Returns Analysis**
(Model TML, two facts sharing `dim_product`), with liveboards, an answer, and generated SQL per visualization.

## Scenarios

| # | Scenario | Strategy build | Expected |
|---|---|---|---|
| S01 | Clean Worksheet lift-and-shift | lookup tables | READY |
| S02 | Clean Model lift-and-shift, renames via mapping, REST JSON input | lookup tables | READY |
| S03 | Benign restructure into one Freeform SQL | Freeform SQL | READY |
| S04 | Freeform SQL joins order lines → fan-out | Freeform SQL | JN-04 UNVERIFIED → FAIL with data (uniqueness of `fact_order_lines.order_id` is not in the package's metadata) |
| S05 | LEFT → INNER customer join, orphans exist | Freeform SQL | JN-01 |
| S06 | LEFT → INNER date join, no orphans (latent) | Freeform SQL | JN-01 latent |
| S07 | COUNT instead of COUNT DISTINCT | lookup tables | AG-01 |
| S08 | `group_aggregate` level lost | lookup tables | AG-02 |
| S09 | Average of row ratios vs ratio of sums | lookup tables | AG-03 |
| S10 | Worksheet filter missing from the SQL | Freeform SQL | FL-01 |
| S11 | `safe_divide` (0) vs plain division (NULL) | lookup tables | FX-03 |
| S12 | Progressive INNER vs always-applied INNER | Freeform SQL | JN-06 |
| S13 | Chasm trap: lines × returns via product | Freeform SQL | JN-05, plus JN-01: the SQL's `dim_product LEFT JOIN fact_returns` drops refunds for unknown products |
| S14 | Different formula, same meaning | lookup tables | FX-01 → PASS with data |
| S15 | Dashboard drift: filter, top-N, week start | lookup tables | LN-01, DB-02, DB-05 |
| S16 | Cosmetic only | lookup tables | READY + LOW |
| S17 | Missing KPI used by content, extra KPI | lookup tables | CV-01, DB-01, CV-02 |
| S18 | S08 delivered as Apache Ossie YAML | lookup tables | AG-02, FX-01, JN-01 UNVERIFIED → data |

## Shapes and assumptions

Strategy shapes follow the official REST API samples (`github.com/MicroStrategy/rest-api-docs`):
the model YAML export, per-object REST JSON, and Freeform SQL tables as
`physicalTable.type: "sql"` + `sqlStatement`. These parts are **assumed** until a real export confirms them:

- **A1**: dashboard definition JSON (chapters → pages → visualizations, `definition.grid`, `limit`, chapter `filters`).
- **A2**: lookup-style join semantics are declared with the model property
  `[Joins].[Preserve all final pass result elements]` = outer join, matching ThoughtSpot LEFT joins.
- **A5**: the top-level layout of Apache Ossie YAML (`semantic_model[].datasets / relationships / metrics`).

When a real sanitised export arrives, adjust `inspector_synth/strategy.py` and regenerate.
