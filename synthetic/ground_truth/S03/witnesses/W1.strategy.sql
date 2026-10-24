SELECT
    ff.customer_region AS "Customer Region",
    SUM(ff.order_amount) AS "Revenue",
    SUM(ff.discount_amount) AS "Discount",
    SUM(ff.shipping_amount) AS "Shipping",
    SUM(ff.item_count) AS "Items Sold",
    COUNT(DISTINCT ff.order_id) AS "Order Count",
    COUNT(DISTINCT ff.customer_id) AS "Customer Count",
    SUM(ff.order_amount) / NULLIF(COUNT(DISTINCT ff.order_id), 0) AS "Avg Order Value",
    CASE WHEN SUM(ff.order_amount) = 0 THEN 0 ELSE SUM(ff.discount_amount) / SUM(ff.order_amount) END AS "Discount Rate",
    SUM(ff.order_amount - COALESCE(ff.discount_amount, 0)) AS "Net Revenue"
FROM (
    SELECT
        o.order_id,
        o.order_date,
        o.order_status,
        o.customer_id,
        o.store_id,
        c.customer_name,
        c.segment,
        c.region AS customer_region,
        s.store_name,
        s.city,
        s.region AS store_region,
        s.store_type,
        d.week_start_monday,
        d.month_start,
        d.quarter,
        d.year,
        d.fiscal_year,
        o.order_amount,
        o.discount_amount,
        o.shipping_amount,
        o.item_count
    FROM analytics.retail.fact_orders o
    LEFT JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
    LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
    LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
    WHERE o.order_status <> 'Cancelled'
) ff
GROUP BY 1
ORDER BY "Customer Region"
