-- ThoughtSpot semantics for liveboard 'Sales Overview', visualization 'Average Order Value'
SELECT
    SUM(o.order_amount) / NULLIF(COUNT(DISTINCT o.order_id), 0) AS "Avg Order Value"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
