-- Placeholder init script, runs once on first container start.
-- Real SPM entity schema (projects, users, assignments, document links)
-- comes later as part of app design — this just prepares the database.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
