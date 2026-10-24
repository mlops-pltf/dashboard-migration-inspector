SELECT
    o.order_status AS "Order Status",
    CASE WHEN SUM(o.order_amount) = 0 THEN 0 ELSE SUM(o.discount_amount) / SUM(o.order_amount) END AS "Discount Rate"
FROM analytics.retail.fact_orders o
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Order Status"
