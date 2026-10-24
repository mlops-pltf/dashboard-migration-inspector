WITH q1 AS (
    SELECT
        p.category AS "Category",
        SUM(l.line_amount) AS "Line Revenue"
    FROM analytics.retail.fact_order_lines l
    LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id
    GROUP BY 1
),
q2 AS (
    SELECT
        p.category AS "Category",
        SUM(r.refund_amount) AS "Refund Amount"
    FROM analytics.retail.fact_returns r
    LEFT JOIN analytics.retail.dim_product p ON p.product_id = r.product_id
    GROUP BY 1
),
merged AS (
    SELECT "Category", MAX("Line Revenue") AS "Line Revenue", MAX("Refund Amount") AS "Refund Amount"
    FROM (
        SELECT "Category", "Line Revenue", NULL AS "Refund Amount" FROM q1
        UNION ALL
        SELECT "Category", NULL AS "Line Revenue", "Refund Amount" FROM q2
    ) u
    GROUP BY 1
)
SELECT
    merged."Category",
    merged."Line Revenue",
    merged."Refund Amount"
FROM merged
ORDER BY "Category"
