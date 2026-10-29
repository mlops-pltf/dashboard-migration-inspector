-- ThoughtSpot semantics for liveboard 'Product Performance', visualization 'Monthly Units'
SELECT
    d.month_start AS "Order Month",
    SUM(l.quantity) AS "Units",
    SUM(l.line_amount) / NULLIF(SUM(l.quantity), 0) AS "Avg Selling Price"
FROM analytics.retail.fact_order_lines l
LEFT JOIN analytics.retail.fact_orders o ON o.order_id = l.order_id
LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
GROUP BY 1
ORDER BY "Order Month" ASC
