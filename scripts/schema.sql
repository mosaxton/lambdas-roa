-- RightOfAccess Lambda schema
-- Derived from vendor/app.rightofaccess/prisma/schema.prisma
-- Safe to re-apply: tables use IF NOT EXISTS; enum types use DO-block guards.

-- ── Enum types ────────────────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE case_status AS ENUM (
        'PENDING_AUTH', 'AUTHORIZED', 'PROCESSING', 'COMPLETE', 'EXPIRED', 'ERROR'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE payer_status AS ENUM ('HEALTHY', 'DEGRADED', 'DOWN', 'UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('ADMIN', 'PARALEGAL');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE delivery_method AS ENUM ('SMS', 'EMAIL', 'BOTH');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── firms ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS firms (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT        NOT NULL,
    clerk_org_id        TEXT        NOT NULL UNIQUE,
    stripe_customer_id  TEXT,
    pilot_cases_used    INTEGER     NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── users ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id       UUID        NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    clerk_user_id TEXT        NOT NULL UNIQUE,
    role          user_role   NOT NULL DEFAULT 'PARALEGAL',
    email         TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_firm_id_idx ON users(firm_id);

-- ── cases ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cases (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id         UUID        NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    created_by      UUID        NOT NULL REFERENCES users(id),
    claimant_name   BYTEA       NOT NULL,
    dob             BYTEA       NOT NULL,
    status          case_status NOT NULL DEFAULT 'PENDING_AUTH',
    payer_slug      TEXT,
    claimant_phone  BYTEA,
    claimant_email  BYTEA,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cases_firm_id_idx        ON cases(firm_id);
CREATE INDEX IF NOT EXISTS cases_status_idx         ON cases(status);
CREATE INDEX IF NOT EXISTS cases_firm_id_status_idx ON cases(firm_id, status);

-- ── payer_tokens ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payer_tokens (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id              UUID        NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    payer_slug           TEXT        NOT NULL,
    access_token_enc     BYTEA       NOT NULL,
    refresh_token_enc    BYTEA,
    patient_fhir_id_enc  BYTEA,
    expires_at           TIMESTAMPTZ NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, payer_slug)
);

CREATE INDEX IF NOT EXISTS payer_tokens_case_id_idx ON payer_tokens(case_id);

-- ── eob_raw ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS eob_raw (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           UUID        NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    fhir_resource_id  TEXT        NOT NULL,
    raw_json_enc      BYTEA       NOT NULL,
    pulled_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, fhir_resource_id)
);

CREATE INDEX IF NOT EXISTS eob_raw_case_id_idx ON eob_raw(case_id);

-- ── providers ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS providers (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id      UUID        NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    npi          TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    specialty    TEXT,
    address      TEXT,
    phone        TEXT,
    resolved_at  TIMESTAMPTZ,
    UNIQUE (case_id, npi)
);

CREATE INDEX IF NOT EXISTS providers_case_id_idx ON providers(case_id);

-- ── encounters ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS encounters (
    id               UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id          UUID    NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    provider_id      UUID    NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    date_of_service  DATE    NOT NULL,
    dx_codes         JSONB   NOT NULL DEFAULT '[]',
    cpt_codes        JSONB   NOT NULL DEFAULT '[]',
    facility_name    TEXT
);

CREATE INDEX IF NOT EXISTS encounters_case_id_idx             ON encounters(case_id);
CREATE INDEX IF NOT EXISTS encounters_provider_id_idx         ON encounters(provider_id);
CREATE INDEX IF NOT EXISTS encounters_case_id_date_idx        ON encounters(case_id, date_of_service);

-- ── prescriptions ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS prescriptions (
    id            UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       UUID  NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    provider_id   UUID  NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    drug_name     TEXT  NOT NULL,
    dosage        TEXT,
    fill_date     DATE  NOT NULL,
    pharmacy_name TEXT,
    pharmacy_npi  TEXT
);

CREATE INDEX IF NOT EXISTS prescriptions_case_id_idx    ON prescriptions(case_id);
CREATE INDEX IF NOT EXISTS prescriptions_provider_id_idx ON prescriptions(provider_id);

-- ── nppes_cache ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nppes_cache (
    id          TEXT        PRIMARY KEY,
    npi         TEXT        NOT NULL UNIQUE,
    data        JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── payer_health ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payer_health (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    payer_slug           TEXT         NOT NULL UNIQUE,
    status               payer_status NOT NULL DEFAULT 'UNKNOWN',
    last_check           TIMESTAMPTZ,
    response_time_ms     INTEGER,
    consecutive_failures INTEGER      NOT NULL DEFAULT 0,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ── audit_log ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit_log (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID,
    firm_id        UUID,
    action         TEXT        NOT NULL,
    resource_type  TEXT        NOT NULL,
    resource_id    TEXT,
    ip_address     TEXT,
    metadata       JSONB,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_user_id_idx              ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS audit_log_firm_id_idx              ON audit_log(firm_id);
CREATE INDEX IF NOT EXISTS audit_log_resource_type_id_idx     ON audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS audit_log_timestamp_idx            ON audit_log(timestamp);
