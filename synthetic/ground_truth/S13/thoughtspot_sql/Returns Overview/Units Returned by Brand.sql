-- ThoughtSpot semantics for liveboard 'Returns Overview', visualization 'Units Returned by Brand'
WITH q1 AS (
    SELECT
        p.brand AS "Brand",
        SUM(l.quantity) AS "Units Sold"
    FROM analytics.retail.fact_order_lines l
    LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id
    GROUP BY 1
),
q2 AS (
    SELECT
        p.brand AS "Brand",
        SUM(r.return_qty) AS "Units Returned"
    FROM analytics.retail.fact_returns r
    LEFT JOIN analytics.retail.dim_product p ON p.product_id = r.product_id
    GROUP BY 1
),
merged AS (
    SELECT "Brand", MAX("Units Sold") AS "Units Sold", MAX("Units Returned") AS "Units Returned"
    FROM (
        SELECT "Brand", "Units Sold", NULL AS "Units Returned" FROM q1
        UNION ALL
        SELECT "Brand", NULL AS "Units Sold", "Units Returned" FROM q2
    ) u
    GROUP BY 1
)
SELECT
    merged."Brand",
    merged."Units Sold",
    merged."Units Returned"
FROM merged
ORDER BY "Units Returned" DESC
