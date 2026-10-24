-- ThoughtSpot SQL for liveboard 'Sales Overview', visualization 'Region vs Store Type'
WITH q1 AS (
    SELECT
        s.region AS "Store Region",
        s.store_type AS "Store Type",
        SUM(o.order_amount) AS "Revenue"
    FROM analytics.retail.fact_orders o
    LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
    LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
    WHERE o.order_status NOT IN ('Cancelled')
      AND d.year IN (2025)
    GROUP BY 1, 2
),
lvl1 AS (
    SELECT
        s.region AS "Store Region",
        SUM(o.order_amount) AS "Region Revenue"
    FROM analytics.retail.fact_orders o
    LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
    LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
    WHERE o.order_status NOT IN ('Cancelled')
      AND d.year IN (2025)
    GROUP BY 1
)
SELECT
    q1."Store Region",
    q1."Store Type",
    q1."Revenue",
    lvl1."Region Revenue"
FROM q1
LEFT JOIN lvl1 ON lvl1."Store Region" IS NOT DISTINCT FROM q1."Store Region"
ORDER BY "Store Region" ASC
