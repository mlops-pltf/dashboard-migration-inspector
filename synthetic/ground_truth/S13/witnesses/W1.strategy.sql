SELECT
    ff.category AS "Category",
    SUM(ff.line_amount) AS "Line Revenue",
    SUM(ff.refund_amount) AS "Refund Amount"
FROM (
    SELECT
        p.product_id,
        p.category,
        p.subcategory,
        p.brand,
        l.line_amount,
        l.quantity,
        r.refund_amount,
        r.return_qty
    FROM analytics.retail.fact_order_lines l
    LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id
    LEFT JOIN analytics.retail.fact_returns r ON p.product_id = r.product_id
) ff
GROUP BY 1
ORDER BY "Category"
