-- ThoughtSpot SQL for liveboard 'Sales Overview', visualization 'Shipping by Customer Region'
SELECT
    c.region AS "Customer Region",
    SUM(o.shipping_amount) AS "Shipping",
    COUNT(DISTINCT o.customer_id) AS "Customer Count"
FROM analytics.retail.fact_orders o
JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
WHERE o.order_status NOT IN ('Cancelled')
  AND d.year IN (2025)
GROUP BY 1
ORDER BY "Shipping" DESC
