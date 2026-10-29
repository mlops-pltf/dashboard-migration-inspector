-- ThoughtSpot semantics for liveboard 'Sales Overview', visualization 'Weekly Net Revenue'
SELECT
    d.week_start_monday AS "Order Week",
    SUM(o.order_amount - COALESCE(o.discount_amount, 0)) AS "Net Revenue",
    COUNT(DISTINCT o.order_id) AS "Order Count"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
GROUP BY 1
ORDER BY "Order Week" ASC
