COPY retail.dim_date FROM '/seed/data/dim_date.csv' WITH (FORMAT csv, HEADER true, NULL '');
COPY retail.dim_customer FROM '/seed/data/dim_customer.csv' WITH (FORMAT csv, HEADER true, NULL '');
COPY retail.dim_store FROM '/seed/data/dim_store.csv' WITH (FORMAT csv, HEADER true, NULL '');
COPY retail.dim_product FROM '/seed/data/dim_product.csv' WITH (FORMAT csv, HEADER true, NULL '');
COPY retail.fact_orders FROM '/seed/data/fact_orders.csv' WITH (FORMAT csv, HEADER true, NULL '');
COPY retail.fact_order_lines FROM '/seed/data/fact_order_lines.csv' WITH (FORMAT csv, HEADER true, NULL '');
COPY retail.fact_returns FROM '/seed/data/fact_returns.csv' WITH (FORMAT csv, HEADER true, NULL '');
ANALYZE;
