-- ThoughtSpot SQL for liveboard 'Product Performance', visualization 'Units by Category'
SELECT
    p.category AS "Category",
    SUM(l.quantity) AS "Units"
FROM analytics.retail.fact_order_lines l
LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id
GROUP BY 1
ORDER BY "Units" DESC
