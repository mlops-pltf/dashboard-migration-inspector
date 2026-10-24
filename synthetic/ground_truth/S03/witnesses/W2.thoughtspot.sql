SELECT
    SUM(o.order_amount) AS "Revenue",
    SUM(o.discount_amount) AS "Discount",
    SUM(o.shipping_amount) AS "Shipping",
    SUM(o.item_count) AS "Items Sold",
    COUNT(DISTINCT o.order_id) AS "Order Count",
    COUNT(DISTINCT o.customer_id) AS "Customer Count",
    SUM(o.order_amount) / NULLIF(COUNT(DISTINCT o.order_id), 0) AS "Avg Order Value",
    CASE WHEN SUM(o.order_amount) = 0 THEN 0 ELSE SUM(o.discount_amount) / SUM(o.order_amount) END AS "Discount Rate",
    SUM(o.order_amount - COALESCE(o.discount_amount, 0)) AS "Net Revenue"
FROM analytics.retail.fact_orders o
WHERE o.order_status NOT IN ('Cancelled')
