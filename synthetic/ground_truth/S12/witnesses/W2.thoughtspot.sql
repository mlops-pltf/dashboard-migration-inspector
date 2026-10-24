SELECT
    c.region AS "Customer Region",
    SUM(o.order_amount) AS "Revenue"
FROM analytics.retail.fact_orders o
JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Customer Region"
