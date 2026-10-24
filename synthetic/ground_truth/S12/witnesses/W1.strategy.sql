SELECT
    ff.store_region AS "Store Region",
    SUM(ff.order_amount) AS "Revenue"
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
    JOIN analytics.retail.dim_customer c ON c.customer_id = o.customer_id
    LEFT JOIN analytics.retail.dim_store s ON s.store_id = o.store_id
    LEFT JOIN analytics.retail.dim_date d ON d.date_key = o.order_date
    WHERE o.order_status <> 'Cancelled'
) ff
GROUP BY 1
ORDER BY "Store Region"
