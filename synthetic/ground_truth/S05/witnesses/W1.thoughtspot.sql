SELECT
    SUM(o.order_amount) AS "Revenue",
    COUNT(DISTINCT o.order_id) AS "Order Count"
FROM analytics.retail.fact_orders o
WHERE o.order_status NOT IN ('Cancelled')
