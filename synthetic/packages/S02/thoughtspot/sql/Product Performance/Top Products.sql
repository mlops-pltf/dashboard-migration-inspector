-- ThoughtSpot SQL for liveboard 'Product Performance', visualization 'Top Products'
SELECT
    p.product_name AS "Product Name",
    SUM(l.line_amount) AS "Line Revenue"
FROM analytics.retail.fact_order_lines l
LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id
GROUP BY 1
ORDER BY "Line Revenue" DESC
LIMIT 10
