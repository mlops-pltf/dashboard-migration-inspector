SELECT
    s.region AS "Store Region",
    SUM(o.order_amount) AS "Revenue",
    COUNT(DISTINCT o.order_id) AS "Order Count"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Store Region"
