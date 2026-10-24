-- Read-only login used by data_agent (mirrors the Redshift service account).
CREATE ROLE inspector_ro LOGIN PASSWORD 'inspector_ro';
GRANT CONNECT ON DATABASE analytics TO inspector_ro;
GRANT USAGE ON SCHEMA retail TO inspector_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA retail TO inspector_ro;
ALTER ROLE inspector_ro SET statement_timeout = '60s';
ALTER ROLE inspector_ro SET default_transaction_read_only = on;
