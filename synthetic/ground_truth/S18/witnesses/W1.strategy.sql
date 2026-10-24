SELECT
    s.region AS "Store Region",
    s.store_type AS "Store Type",
    SUM(o.order_amount) AS "Revenue",
    SUM(o.order_amount) AS "Region Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1, 2
ORDER BY "Store Region", "Store Type"
