-- ThoughtSpot semantics for liveboard 'Sales Overview', visualization 'Revenue by Store Region'
SELECT
    s.region AS "Store Region",
    SUM(o.order_amount) AS "Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
GROUP BY 1
ORDER BY "Revenue" DESC
