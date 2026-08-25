-- RUC System PostgreSQL setup template for a new Windows Mini-PC.
-- Copy this file before use and replace every placeholder.
-- Do not commit real passwords.
-- Do not run against an existing production database without a backup.

-- Run these commands as a PostgreSQL administrative role.

CREATE ROLE ruc_app LOGIN PASSWORD 'REPLACE_WITH_STRONG_LOCAL_PASSWORD';

ALTER ROLE ruc_app
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT;

CREATE DATABASE ruc_system OWNER postgres;

\connect ruc_system

GRANT CONNECT, TEMPORARY ON DATABASE ruc_system TO ruc_app;
GRANT USAGE ON SCHEMA public TO ruc_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ruc_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO ruc_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ruc_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ruc_app;

-- After restoring a production dump, rerun the GRANT statements above.
