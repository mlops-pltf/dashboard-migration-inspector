-- ThoughtSpot semantics for liveboard 'Sales Overview', visualization 'Monthly Revenue Trend'
SELECT
    d.month_start AS "Order Month",
    SUM(o.order_amount) AS "Revenue",
    COUNT(DISTINCT o.order_id) AS "Order Count"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
GROUP BY 1
ORDER BY "Order Month" ASC
