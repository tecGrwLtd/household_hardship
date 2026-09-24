-- Platform accounts with roles (admin, caseworker). For databases created
-- before this change; safe to run more than once:
--   docker compose exec -T db psql -U hardship_app -d hardship_platform < db/migrations/002_users_and_roles.sql
-- The API creates the admin account from HARDSHIP_ADMIN_USER / _PASSWORD on
-- start-up when none exists; `python db/load_data.py` adds the demo caseworker.

BEGIN;
DO $$ BEGIN
    CREATE TYPE user_role_enum AS ENUM ('admin', 'caseworker');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS app_users (
    user_id         SERIAL PRIMARY KEY,
    username        VARCHAR(50) NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    role            user_role_enum NOT NULL,
    caseworker_id   INTEGER REFERENCES caseworkers(caseworker_id),
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT caseworker_account_linked CHECK (role <> 'caseworker' OR caseworker_id IS NOT NULL)
);

-- the demo caseworker (development data only)
INSERT INTO app_users (username, display_name, password_hash, role, caseworker_id)
SELECT 'uwase', display_name, crypt('caseworker-dev-only', gen_salt('bf', 10)), 'caseworker', caseworker_id
FROM caseworkers WHERE caseworker_id = 1
ON CONFLICT (username) DO NOTHING;
COMMIT;
