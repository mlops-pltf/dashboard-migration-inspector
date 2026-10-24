SELECT
    d.week_start_sunday AS "Order Week",
    SUM(o.order_amount - COALESCE(o.discount_amount, 0)) AS "Net Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Order Week"
