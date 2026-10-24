SELECT
    d.week_start_monday AS "Order Week",
    c.segment AS "Customer Segment",
    SUM(o.order_amount) - COALESCE(SUM(o.discount_amount), 0) AS "Net Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
LEFT JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1, 2
ORDER BY "Order Week", "Customer Segment"
