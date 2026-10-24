-- ThoughtSpot SQL for answer 'Revenue by Customer Segment', visualization 'Revenue by Customer Segment'
SELECT
    c.segment AS "Customer Segment",
    SUM(o.order_amount) AS "Revenue",
    COUNT(DISTINCT o.customer_id) AS "Customer Count"
FROM analytics.retail.fact_orders o
LEFT JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
WHERE o.order_status NOT IN ('Cancelled')
GROUP BY 1
ORDER BY "Revenue" DESC
