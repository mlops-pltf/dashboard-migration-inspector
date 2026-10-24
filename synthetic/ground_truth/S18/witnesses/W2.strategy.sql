SELECT
    c.segment AS "Customer Segment",
    SUM(o.order_amount - COALESCE(o.discount_amount, 0)) AS "Net Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Customer Segment"
