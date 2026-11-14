-- Runs once, when the data volume is first initialised.
-- Extensions are also created by Alembic (idempotently) so that managed
-- PostgreSQL services without init scripts work the same way.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Separate database for the automated test suite and migration rehearsals.
CREATE DATABASE autohub_test;
\connect autohub_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
