"""
shared/db.py — the ONLY place in the roa-lambdas codebase where raw SQL lives.

Lambda handlers call these helpers; they never call cursor.execute directly.
All helpers accept an open psycopg2 connection as their first argument so that
callers (and tests) control transaction boundaries.

Connection lifecycle (for Lambda handlers):
  Use the get_connection() context manager once per invocation. It maintains a
  module-level connection that is reused across warm Lambda invocations and
  reconnected automatically if the TCP connection drops between invocations.

PHI discipline:
  - Never log encrypted bytes columns (tokens, claimant_name, dob, etc.).
  - Never return raw bytes to callers that don't need them — return full rows
    only where the caller explicitly needs every field (get_payer_token).
"""

import logging
import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any

import psycopg2
import psycopg2.extensions
import psycopg2.extras

logger = logging.getLogger(__name__)

# Module-level connection reused across warm Lambda invocations.
_connection: psycopg2.extensions.connection | None = None


# ── Connection management ─────────────────────────────────────────────────────


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable is not set")
    return url


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """Lazy-init a module-level connection, reused across warm invocations.

    Commits on clean exit, rolls back on exception, reconnects if the
    connection is closed (e.g. after a Lambda container pause).
    """
    global _connection
    try:
        if _connection is None or _connection.closed:
            _connection = psycopg2.connect(
                _get_db_url(),
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
        yield _connection
        _connection.commit()
    except Exception:
        if _connection and not _connection.closed:
            _connection.rollback()
        raise


# ── Payer token helpers ───────────────────────────────────────────────────────


def get_payer_token(
    conn: psycopg2.extensions.connection,
    case_id: str,
    payer_slug: str,
) -> dict[str, Any] | None:
    """Return the full payer_token row for (case_id, payer_slug), or None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, case_id, payer_slug, access_token_enc, refresh_token_enc,
                   patient_fhir_id_enc, expires_at
            FROM payer_tokens
            WHERE case_id = %s AND payer_slug = %s
            """,
            (case_id, payer_slug),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_payer_token(
    conn: psycopg2.extensions.connection,
    token_id: str,
    access_token_enc: bytes,
    refresh_token_enc: bytes | None,
    expires_at: datetime,
) -> None:
    """Update encrypted token fields and expiry on an existing payer_token row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE payer_tokens
            SET access_token_enc  = %s,
                refresh_token_enc = %s,
                expires_at        = %s
            WHERE id = %s
            """,
            (access_token_enc, refresh_token_enc, expires_at, token_id),
        )


def list_expiring_tokens(
    conn: psycopg2.extensions.connection,
    window_minutes: int = 20,
) -> list[dict[str, Any]]:
    """Return tokens expiring within window_minutes but not already expired."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, case_id, payer_slug, access_token_enc, refresh_token_enc,
                   patient_fhir_id_enc, expires_at
            FROM payer_tokens
            WHERE expires_at < NOW() + %s
              AND expires_at > NOW()
            """,
            (timedelta(minutes=window_minutes),),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── Case helpers ──────────────────────────────────────────────────────────────


def get_case(
    conn: psycopg2.extensions.connection,
    case_id: str,
) -> dict[str, Any] | None:
    """Return id, firm_id, status, payer_slug, updated_at for a case, or None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, firm_id, status, payer_slug, updated_at
            FROM cases
            WHERE id = %s
            """,
            (case_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_case_status(
    conn: psycopg2.extensions.connection,
    case_id: str,
    status: str,
) -> None:
    """Update case status and set updated_at explicitly (no ORM magic here)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cases
            SET status     = %s::case_status,
                updated_at = now()
            WHERE id = %s
            """,
            (status, case_id),
        )


# ── EOB raw helpers ───────────────────────────────────────────────────────────


def upsert_eob_raw(
    conn: psycopg2.extensions.connection,
    case_id: str,
    fhir_resource_id: str,
    raw_json_enc: bytes,
) -> None:
    """Insert or update a raw encrypted EOB record."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eob_raw (id, case_id, fhir_resource_id, raw_json_enc, pulled_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (case_id, fhir_resource_id)
            DO UPDATE SET
                raw_json_enc = EXCLUDED.raw_json_enc,
                pulled_at    = now()
            """,
            (str(uuid.uuid4()), case_id, fhir_resource_id, raw_json_enc),
        )


# ── Provider helpers ──────────────────────────────────────────────────────────


def upsert_provider(
    conn: psycopg2.extensions.connection,
    case_id: str,
    npi: str,
    name: str,
    specialty: str | None,
    address: str | None,
    phone: str | None,
) -> str:
    """Insert or update a provider row. Returns the UUID string of the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO providers (id, case_id, npi, name, specialty, address, phone, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (case_id, npi)
            DO UPDATE SET
                name        = EXCLUDED.name,
                specialty   = EXCLUDED.specialty,
                address     = EXCLUDED.address,
                phone       = EXCLUDED.phone,
                resolved_at = now()
            RETURNING id
            """,
            (str(uuid.uuid4()), case_id, npi, name, specialty, address, phone),
        )
        row = cur.fetchone()
    return str(row["id"])


# ── Encounter helpers ─────────────────────────────────────────────────────────


def insert_encounter(
    conn: psycopg2.extensions.connection,
    case_id: str,
    provider_id: str,
    date_of_service: date | str,
    dx_codes: list[str],
    cpt_codes: list[str],
    facility_name: str | None,
) -> None:
    """Insert a single encounter row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO encounters
                (id, case_id, provider_id, date_of_service, dx_codes, cpt_codes, facility_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                case_id,
                provider_id,
                date_of_service,
                psycopg2.extras.Json(dx_codes),
                psycopg2.extras.Json(cpt_codes),
                facility_name,
            ),
        )


# ── Prescription helpers ──────────────────────────────────────────────────────


def insert_prescription(
    conn: psycopg2.extensions.connection,
    case_id: str,
    provider_id: str,
    drug_name: str,
    dosage: str | None,
    fill_date: date | str,
    pharmacy_name: str | None,
    pharmacy_npi: str | None,
) -> None:
    """Insert a single prescription row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prescriptions
                (id, case_id, provider_id, drug_name, dosage,
                 fill_date, pharmacy_name, pharmacy_npi)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                case_id,
                provider_id,
                drug_name,
                dosage,
                fill_date,
                pharmacy_name,
                pharmacy_npi,
            ),
        )


# ── NPPES cache helpers ───────────────────────────────────────────────────────


def get_nppes_cache(
    conn: psycopg2.extensions.connection,
    npi: str,
) -> dict[str, Any] | None:
    """Return the cached NPPES row for an NPI, or None on a cache miss."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, npi, data, created_at, updated_at FROM nppes_cache WHERE npi = %s",
            (npi,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_nppes_cache(
    conn: psycopg2.extensions.connection,
    npi: str,
    data: dict[str, Any],
) -> None:
    """Insert or refresh an NPPES cache entry. Column is `data` (JSONB).

    The id column is TEXT (Prisma uses cuid() on the TS side). The Lambda
    generates a UUID string which is unique enough for this purpose.
    """
    row_id = str(uuid.uuid4())

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO nppes_cache (id, npi, data, created_at, updated_at)
            VALUES (%s, %s, %s, now(), now())
            ON CONFLICT (npi)
            DO UPDATE SET
                data       = EXCLUDED.data,
                updated_at = now()
            """,
            (row_id, npi, psycopg2.extras.Json(data)),
        )


# ── Payer health helpers ──────────────────────────────────────────────────────


def update_payer_health(
    conn: psycopg2.extensions.connection,
    payer_slug: str,
    status: str,
    response_time_ms: int | None,
    failures_delta: int,
) -> None:
    """Upsert a payer_health row.

    failures_delta semantics:
      > 0  — increment consecutive_failures by that amount
      < 0  — reset consecutive_failures to 0
      = 0  — leave consecutive_failures unchanged
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payer_health
                (id, payer_slug, status, last_check,
                 response_time_ms, consecutive_failures, updated_at)
            VALUES (%s, %s, %s::payer_status, now(), %s, GREATEST(0, %s), now())
            ON CONFLICT (payer_slug)
            DO UPDATE SET
                status               = EXCLUDED.status,
                last_check           = now(),
                response_time_ms     = EXCLUDED.response_time_ms,
                consecutive_failures = CASE
                    WHEN %s < 0 THEN 0
                    ELSE payer_health.consecutive_failures + %s
                END,
                updated_at           = now()
            """,
            (
                str(uuid.uuid4()),
                payer_slug,
                status,
                response_time_ms,
                failures_delta,  # INSERT: GREATEST(0, delta) — on first insert use delta as seed
                failures_delta,  # UPDATE CASE: if negative reset to 0
                failures_delta,  # UPDATE CASE: else increment by delta
            ),
        )


# ── Audit log helpers ─────────────────────────────────────────────────────────


def insert_audit_log(
    conn: psycopg2.extensions.connection,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    firm_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an audit log entry. user_id and ip_address are NULL (Lambda context)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_log
                (id, user_id, firm_id, action, resource_type, resource_id,
                 ip_address, metadata, timestamp)
            VALUES (%s, NULL, %s, %s, %s, %s, NULL, %s, now())
            """,
            (
                str(uuid.uuid4()),
                firm_id,
                action,
                resource_type,
                resource_id,
                psycopg2.extras.Json(metadata) if metadata is not None else None,
            ),
        )
