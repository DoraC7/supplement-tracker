-- ONE-TIME provisioning for a fresh database/schema (PostgreSQL 14+).
-- Run as a dedicated migration owner with CREATEROLE, never as the app LOGIN.
-- Deliberately not an automatic migration or a runtime startup script.
-- Create credential-bearing LOGIN roles and grant supplemind_app membership
-- outside this repository, with permission to SET ROLE (PG16+: SET TRUE).
-- The adapter explicitly SET ROLE supplemind_app; the LOGIN must not own objects
-- or have other privileged memberships. Never grant this role to web/API roles.
-- Keep supplemind OUT of PostgREST/GraphQL/exposed-schema configuration. This is
-- a private single-owner backend, NOT a multi-tenant/RLS or public-data API.
-- Use a private network/firewall and TLS. Do not use superuser/service API keys
-- as application credentials. Admin/superuser access is outside this boundary.
-- Default ACL changes below apply to objects created by THIS migration owner;
-- all future migrations must use that owner or repeat this privilege policy.
BEGIN;

DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supplemind_app') THEN
        CREATE ROLE supplemind_app NOLOGIN NOINHERIT;
    END IF;
END
$role$;
ALTER ROLE supplemind_app NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE SCHEMA supplemind;

CREATE TABLE supplemind.Supplements (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL,
    stock_quantity DOUBLE PRECISION NOT NULL
        CHECK (stock_quantity >= 0 AND stock_quantity < 'Infinity'::DOUBLE PRECISION),
    warning_level DOUBLE PRECISION NOT NULL
        CHECK (warning_level >= 0 AND warning_level < 'Infinity'::DOUBLE PRECISION),
    expiry_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE supplemind.Logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplement_id BIGINT NOT NULL REFERENCES supplemind.Supplements(id) ON DELETE CASCADE,
    taken_at TEXT NOT NULL,
    dosage DOUBLE PRECISION NOT NULL
        CHECK (dosage > 0 AND dosage < 'Infinity'::DOUBLE PRECISION)
);

CREATE TABLE supplemind.Conflicts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplement_id_a BIGINT NOT NULL REFERENCES supplemind.Supplements(id) ON DELETE CASCADE,
    supplement_id_b BIGINT NOT NULL REFERENCES supplemind.Supplements(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (supplement_id_a, supplement_id_b)
);

CREATE TABLE supplemind.TrackerProfiles (
    supplement_id BIGINT PRIMARY KEY REFERENCES supplemind.Supplements(id),
    profile TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);

CREATE TABLE supplemind.DoseEvents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplement_id BIGINT NOT NULL REFERENCES supplemind.Supplements(id),
    day TEXT NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('taken', 'skipped', 'undone')),
    dose DOUBLE PRECISION NOT NULL
        CHECK (dose > 0 AND dose < 'Infinity'::DOUBLE PRECISION),
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    log_id BIGINT REFERENCES supplemind.Logs(id) ON DELETE SET NULL,
    undone_at TEXT
);
CREATE UNIQUE INDEX unique_active_dose
    ON supplemind.DoseEvents (supplement_id, day, slot)
    WHERE status != 'undone' AND slot != 'as_needed';

REVOKE ALL ON SCHEMA supplemind FROM PUBLIC, supplemind_app;
REVOKE ALL ON ALL TABLES IN SCHEMA supplemind FROM PUBLIC, supplemind_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA supplemind FROM PUBLIC, supplemind_app;
GRANT USAGE ON SCHEMA supplemind TO supplemind_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA supplemind TO supplemind_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA supplemind TO supplemind_app;

-- Global default revocations are necessary: per-schema revocations cannot undo
-- global default grants. Use a dedicated migration owner (affects its future
-- objects elsewhere too). No CREATE, TRUNCATE, REFERENCES, or grant options.
ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA supplemind
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO supplemind_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA supplemind
    GRANT USAGE ON SEQUENCES TO supplemind_app;

-- Supabase roles are optional on ordinary PostgreSQL. When present, revoke
-- explicitly, rather than relying only on the absence of PUBLIC grants.
DO $api$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            IF pg_has_role(api_role, 'supplemind_app', 'MEMBER') THEN
                RAISE EXCEPTION 'API roles must not be members of supplemind_app';
            END IF;
            EXECUTE format('REVOKE ALL ON SCHEMA supplemind FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA supplemind FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA supplemind FROM %I', api_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM %I', api_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES REVOKE ALL ON SEQUENCES FROM %I', api_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM %I', api_role);
        END IF;
    END LOOP;
END
$api$;

COMMIT;