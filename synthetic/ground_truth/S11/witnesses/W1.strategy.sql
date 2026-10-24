SELECT
    o.order_status AS "Order Status",
    SUM(o.discount_amount) / NULLIF(SUM(o.order_amount), 0) AS "Discount Rate"
FROM analytics.retail.fact_orders o
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Order Status"
