-- ThoughtSpot semantics for liveboard 'Sales Overview', visualization 'Top Customers'
SELECT
    c.customer_name AS "Customer Name",
    SUM(o.order_amount) AS "Revenue"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
GROUP BY 1
ORDER BY "Revenue" DESC
LIMIT 10
