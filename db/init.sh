#!/bin/bash
# Runs once, on first initialization of the data directory.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;

    REVOKE CREATE ON SCHEMA public FROM PUBLIC;

    CREATE ROLE wallet_svc LOGIN PASSWORD '${WALLET_DB_PASSWORD}';
    CREATE SCHEMA wallet AUTHORIZATION wallet_svc;
    ALTER ROLE wallet_svc SET search_path = wallet;

    CREATE ROLE agent_svc LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
    CREATE SCHEMA agent AUTHORIZATION agent_svc;
    ALTER ROLE agent_svc SET search_path = agent, public;
EOSQL
