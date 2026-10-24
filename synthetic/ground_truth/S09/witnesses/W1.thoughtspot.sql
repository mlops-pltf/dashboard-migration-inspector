SELECT
    p.category AS "Category",
    (SUM(l.line_amount) - SUM(l.line_cost)) / NULLIF(SUM(l.line_amount), 0) AS "Margin %"
FROM analytics.retail.fact_order_lines l
LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id
GROUP BY 1
ORDER BY "Category"
