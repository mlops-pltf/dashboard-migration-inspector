-- ThoughtSpot semantics for liveboard 'Sales Overview', visualization 'Discount Rate by Status'
SELECT
    o.order_status AS "Order Status",
    CASE WHEN SUM(o.order_amount) = 0 THEN 0 ELSE SUM(o.discount_amount) / SUM(o.order_amount) END AS "Discount Rate",
    SUM(o.order_amount) AS "Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
GROUP BY 1
ORDER BY "Order Status" ASC
