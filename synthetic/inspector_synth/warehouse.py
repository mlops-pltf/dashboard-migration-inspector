"""Retail warehouse: physical schema, deterministic data with deliberate traps, and loaders.

Traps (each one makes a specific defect produce a visible numeric difference):
  * orphan customers: ~3% of orders reference customer_id 901-915, absent from dim_customer
  * orphan products: ~2% of order lines reference product_id 9001-9004, absent from dim_product
  * NULL measures: ~30% of discount_amount and ~10% of shipping_amount are NULL
  * zero denominators: 'Replacement' orders have order_amount = 0
  * one-to-many fan-out: every order has 1-5 lines; returns hang off products shared with lines
  * time: order_ts is UTC, order_date is the America/New_York local date; ~8% of orders fall
    within 2h of midnight UTC; dim_date carries both Monday and Sunday week starts and an April fiscal year
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import DB, SCHEMA, SEED

LOCAL_TZ = ZoneInfo("America/New_York")
START, END = date(2024, 1, 1), date(2025, 12, 31)
ORPHAN_CUSTOMERS = range(901, 916)
ORPHAN_PRODUCTS = range(9001, 9005)


@dataclass(frozen=True)
class PhysCol:
    name: str
    sql_type: str  # Redshift/Postgres type
    nullable: bool = False


@dataclass(frozen=True)
class PhysTable:
    name: str
    columns: tuple[PhysCol, ...]
    key: tuple[str, ...]

    def col(self, name: str) -> PhysCol:
        return next(c for c in self.columns if c.name == name)


def _t(name: str, key: tuple[str, ...], *cols: tuple) -> PhysTable:
    return PhysTable(name, tuple(PhysCol(*c) for c in cols), key)


TABLES: dict[str, PhysTable] = {
    t.name: t
    for t in [
        _t("dim_date", ("date_key",),
           ("date_key", "date"), ("day_of_week", "varchar(9)"), ("week_start_monday", "date"),
           ("week_start_sunday", "date"), ("month_start", "date"), ("month_name", "varchar(9)"),
           ("quarter", "varchar(7)"), ("year", "integer"), ("fiscal_year", "integer"),
           ("fiscal_quarter", "varchar(10)")),
        _t("dim_customer", ("customer_id",),
           ("customer_id", "integer"), ("customer_name", "varchar(64)"), ("segment", "varchar(32)"),
           ("region", "varchar(16)"), ("signup_date", "date")),
        _t("dim_store", ("store_id",),
           ("store_id", "integer"), ("store_name", "varchar(64)"), ("city", "varchar(32)"),
           ("state", "varchar(2)"), ("region", "varchar(16)"), ("store_type", "varchar(16)")),
        _t("dim_product", ("product_id",),
           ("product_id", "integer"), ("product_name", "varchar(64)"), ("category", "varchar(32)"),
           ("subcategory", "varchar(32)"), ("brand", "varchar(32)"), ("unit_cost", "decimal(10,2)")),
        _t("fact_orders", ("order_id",),
           ("order_id", "bigint"), ("order_ts", "timestamp"), ("order_date", "date"),
           ("customer_id", "integer"), ("store_id", "integer"), ("order_status", "varchar(16)"),
           ("order_amount", "decimal(12,2)"), ("discount_amount", "decimal(12,2)", True),
           ("shipping_amount", "decimal(10,2)", True), ("item_count", "integer")),
        _t("fact_order_lines", ("line_id",),
           ("line_id", "bigint"), ("order_id", "bigint"), ("line_number", "integer"),
           ("product_id", "integer"), ("quantity", "integer"), ("unit_price", "decimal(10,2)"),
           ("line_amount", "decimal(12,2)"), ("line_cost", "decimal(12,2)")),
        _t("fact_returns", ("return_id",),
           ("return_id", "bigint"), ("order_id", "bigint"), ("product_id", "integer"),
           ("return_date", "date"), ("return_qty", "integer"), ("refund_amount", "decimal(12,2)")),
    ]
}

LOAD_ORDER = ["dim_date", "dim_customer", "dim_store", "dim_product",
              "fact_orders", "fact_order_lines", "fact_returns"]

REGIONS = ["East", "West", "Central", "South"]
SEGMENTS = ["Consumer", "Corporate", "Small Business"]
STORE_TYPES = ["Flagship", "Mall", "Outlet", "Online"]
CITIES = [("Boston", "MA", "East"), ("New York", "NY", "East"), ("Seattle", "WA", "West"),
          ("San Diego", "CA", "West"), ("Denver", "CO", "West"), ("Chicago", "IL", "Central"),
          ("Dallas", "TX", "South"), ("Atlanta", "GA", "South"), ("Miami", "FL", "South"),
          ("Columbus", "OH", "Central")]
CATEGORIES = {
    "Electronics": ["Audio", "Computing", "Phones"],
    "Home": ["Kitchen", "Furniture", "Decor"],
    "Apparel": ["Mens", "Womens", "Kids"],
    "Sports": ["Fitness", "Outdoor", "Team Sports"],
    "Toys": ["Games", "Puzzles", "Outdoor Play"],
}
BRANDS = ["Acme", "Northwind", "Contoso", "Fabrikam", "Tailspin", "Litware"]


def _q(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _fiscal(d: date) -> tuple[int, str]:
    fy = d.year + 1 if d.month >= 4 else d.year  # FY starts in April, named by its end year
    fq = ((d.month - 4) % 12) // 3 + 1
    return fy, f"FY{fy}-Q{fq}"


def generate(seed: int = SEED) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    rows: dict[str, list[dict]] = {name: [] for name in LOAD_ORDER}

    d = date(2023, 12, 1)
    while d <= date(2026, 1, 31):
        fy, fq = _fiscal(d)
        rows["dim_date"].append({
            "date_key": d, "day_of_week": d.strftime("%A"),
            "week_start_monday": d - timedelta(days=d.weekday()),
            "week_start_sunday": d - timedelta(days=(d.weekday() + 1) % 7),
            "month_start": d.replace(day=1), "month_name": d.strftime("%B"),
            "quarter": _q(d), "year": d.year, "fiscal_year": fy, "fiscal_quarter": fq,
        })
        d += timedelta(days=1)

    for cid in range(1, 601):
        rows["dim_customer"].append({
            "customer_id": cid, "customer_name": f"Customer {cid:04d}",
            "segment": rng.choices(SEGMENTS, weights=[6, 3, 2])[0],
            "region": rng.choice(REGIONS),
            "signup_date": date(2021, 1, 1) + timedelta(days=rng.randrange(1400)),
        })

    for sid in range(1, 31):
        city, state, region = CITIES[(sid - 1) % len(CITIES)]
        stype = "Online" if sid % 10 == 0 else rng.choice(STORE_TYPES[:3])
        rows["dim_store"].append({
            "store_id": sid, "store_name": f"{city} #{sid:02d}", "city": city,
            "state": state, "region": region, "store_type": stype,
        })

    products: dict[int, tuple[float, float]] = {}  # id -> (cost, price)
    pid = 1
    for category, subs in CATEGORIES.items():
        for sub in subs:
            for _ in range(10):
                cost = round(rng.uniform(4, 400), 2)
                products[pid] = (cost, round(cost * rng.uniform(1.15, 2.2), 2))
                rows["dim_product"].append({
                    "product_id": pid, "product_name": f"{sub} Item {pid:03d}", "category": category,
                    "subcategory": sub, "brand": rng.choice(BRANDS), "unit_cost": cost,
                })
                pid += 1
    for opid in ORPHAN_PRODUCTS:  # discontinued products: present in facts, missing from the dimension
        cost = round(rng.uniform(10, 120), 2)
        products[opid] = (cost, round(cost * 1.6, 2))
    real_products = [p for p in products if p not in ORPHAN_PRODUCTS]

    span = (END - START).days + 1
    line_id = return_id = 0
    for oid in range(1, 24001):
        day = START + timedelta(days=rng.randrange(span))
        if rng.random() < 0.08:  # cluster near midnight UTC
            t = time(rng.choice([0, 1, 23]), rng.randrange(60), rng.randrange(60))
        else:
            t = time(rng.randrange(24), rng.randrange(60), rng.randrange(60))
        ts = datetime.combine(day, t, tzinfo=timezone.utc)
        status = rng.choices(["Completed", "Returned", "Cancelled", "Replacement", "Pending"],
                             weights=[85, 5, 5, 3, 2])[0]
        customer = rng.choice(ORPHAN_CUSTOMERS) if rng.random() < 0.03 else rng.randrange(1, 601)

        lines = []
        for ln in range(1, rng.randint(1, 5) + 1):
            product = rng.choice(ORPHAN_PRODUCTS) if rng.random() < 0.02 else rng.choice(real_products)
            cost, price = products[product]
            qty = rng.randint(1, 4)
            price = round(price * rng.uniform(0.9, 1.1), 2)
            line_id += 1
            lines.append({
                "line_id": line_id, "order_id": oid, "line_number": ln, "product_id": product,
                "quantity": qty, "unit_price": price,
                "line_amount": round(qty * price, 2), "line_cost": round(qty * cost, 2),
            })
        rows["fact_order_lines"].extend(lines)

        amount = 0.0 if status == "Replacement" else round(sum(l["line_amount"] for l in lines), 2)
        if status == "Replacement":
            discount = 0.0
        else:
            discount = None if rng.random() < 0.30 else round(amount * rng.uniform(0, 0.15), 2)
        shipping = None if rng.random() < 0.10 else rng.choice([0.0, 4.99, 9.99])
        rows["fact_orders"].append({
            "order_id": oid, "order_ts": ts.replace(tzinfo=None), "order_date": ts.astimezone(LOCAL_TZ).date(),
            "customer_id": customer, "store_id": rng.randrange(1, 31), "order_status": status,
            "order_amount": amount, "discount_amount": discount, "shipping_amount": shipping,
            "item_count": sum(l["quantity"] for l in lines),
        })

        if status == "Returned":
            for line in rng.sample(lines, k=min(len(lines), rng.randint(1, 2))):
                qty = rng.randint(1, line["quantity"])
                return_id += 1
                rows["fact_returns"].append({
                    "return_id": return_id, "order_id": oid, "product_id": line["product_id"],
                    "return_date": day + timedelta(days=rng.randint(3, 30)), "return_qty": qty,
                    "refund_amount": round(qty * line["unit_price"], 2),
                })
    return rows


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def ddl(dialect: str = "postgres") -> str:
    out = [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};", ""]
    for name in LOAD_ORDER:
        t = TABLES[name]
        cols = [f"    {c.name} {c.sql_type}{'' if c.nullable else ' NOT NULL'}" for c in t.columns]
        if dialect == "postgres":
            cols.append(f"    PRIMARY KEY ({', '.join(t.key)})")  # informational in Redshift, so omitted there
        out.append(f"CREATE TABLE {SCHEMA}.{name} (\n" + ",\n".join(cols) + "\n);\n")
    return "\n".join(out)


def write(out_dir: Path, seed: int = SEED) -> dict[str, int]:
    """Write CSVs plus Postgres init scripts (docker-entrypoint-initdb.d) and Redshift DDL."""
    rows = generate(seed)
    data_dir, pg_dir = out_dir / "data", out_dir / "postgres"
    data_dir.mkdir(parents=True, exist_ok=True)
    pg_dir.mkdir(parents=True, exist_ok=True)

    for name in LOAD_ORDER:
        cols = [c.name for c in TABLES[name].columns]
        with open(data_dir / f"{name}.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows[name]:
                w.writerow([_fmt(r[c]) for c in cols])

    (pg_dir / "01_schema.sql").write_text(ddl("postgres"))
    (pg_dir / "02_load.sql").write_text("\n".join(
        f"COPY {SCHEMA}.{n} FROM '/seed/data/{n}.csv' WITH (FORMAT csv, HEADER true, NULL '');"
        for n in LOAD_ORDER) + "\nANALYZE;\n")
    (pg_dir / "03_readonly_role.sql").write_text(
        "-- Read-only login used by data_agent (mirrors the Redshift service account).\n"
        "CREATE ROLE inspector_ro LOGIN PASSWORD 'inspector_ro';\n"
        f"GRANT CONNECT ON DATABASE {DB} TO inspector_ro;\n"
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO inspector_ro;\n"
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO inspector_ro;\n"
        "ALTER ROLE inspector_ro SET statement_timeout = '60s';\n"
        "ALTER ROLE inspector_ro SET default_transaction_read_only = on;\n")
    (out_dir / "redshift_ddl.sql").write_text(ddl("redshift"))
    return {n: len(rows[n]) for n in LOAD_ORDER}
