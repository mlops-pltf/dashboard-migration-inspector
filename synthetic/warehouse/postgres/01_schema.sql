CREATE SCHEMA IF NOT EXISTS retail;

CREATE TABLE retail.dim_date (
    date_key date NOT NULL,
    day_of_week varchar(9) NOT NULL,
    week_start_monday date NOT NULL,
    week_start_sunday date NOT NULL,
    month_start date NOT NULL,
    month_name varchar(9) NOT NULL,
    quarter varchar(7) NOT NULL,
    year integer NOT NULL,
    fiscal_year integer NOT NULL,
    fiscal_quarter varchar(10) NOT NULL,
    PRIMARY KEY (date_key)
);

CREATE TABLE retail.dim_customer (
    customer_id integer NOT NULL,
    customer_name varchar(64) NOT NULL,
    segment varchar(32) NOT NULL,
    region varchar(16) NOT NULL,
    signup_date date NOT NULL,
    PRIMARY KEY (customer_id)
);

CREATE TABLE retail.dim_store (
    store_id integer NOT NULL,
    store_name varchar(64) NOT NULL,
    city varchar(32) NOT NULL,
    state varchar(2) NOT NULL,
    region varchar(16) NOT NULL,
    store_type varchar(16) NOT NULL,
    PRIMARY KEY (store_id)
);

CREATE TABLE retail.dim_product (
    product_id integer NOT NULL,
    product_name varchar(64) NOT NULL,
    category varchar(32) NOT NULL,
    subcategory varchar(32) NOT NULL,
    brand varchar(32) NOT NULL,
    unit_cost decimal(10,2) NOT NULL,
    PRIMARY KEY (product_id)
);

CREATE TABLE retail.fact_orders (
    order_id bigint NOT NULL,
    order_ts timestamp NOT NULL,
    order_date date NOT NULL,
    customer_id integer NOT NULL,
    store_id integer NOT NULL,
    order_status varchar(16) NOT NULL,
    order_amount decimal(12,2) NOT NULL,
    discount_amount decimal(12,2),
    shipping_amount decimal(10,2),
    item_count integer NOT NULL,
    PRIMARY KEY (order_id)
);

CREATE TABLE retail.fact_order_lines (
    line_id bigint NOT NULL,
    order_id bigint NOT NULL,
    line_number integer NOT NULL,
    product_id integer NOT NULL,
    quantity integer NOT NULL,
    unit_price decimal(10,2) NOT NULL,
    line_amount decimal(12,2) NOT NULL,
    line_cost decimal(12,2) NOT NULL,
    PRIMARY KEY (line_id)
);

CREATE TABLE retail.fact_returns (
    return_id bigint NOT NULL,
    order_id bigint NOT NULL,
    product_id integer NOT NULL,
    return_date date NOT NULL,
    return_qty integer NOT NULL,
    refund_amount decimal(12,2) NOT NULL,
    PRIMARY KEY (return_id)
);
