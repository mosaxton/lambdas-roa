"""
shared/tests/test_db.py — Integration tests for shared/db.py helpers.

Each test receives a per-test psycopg2 connection that rolls back after the
test, ensuring full isolation without truncating tables between runs.

Seed helpers (_insert_firm, _insert_user, _insert_case) use direct cursor
calls — that is intentional test infrastructure, not production code.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg2.extras

from shared.db import (
    get_case,
    get_nppes_cache,
    get_payer_token,
    insert_audit_log,
    insert_encounter,
    insert_prescription,
    list_expiring_tokens,
    update_case_status,
    update_payer_health,
    update_payer_token,
    upsert_eob_raw,
    upsert_nppes_cache,
    upsert_provider,
)

# ── Seed helpers (test infrastructure only) ───────────────────────────────────


def _insert_firm(cur: psycopg2.extras.RealDictCursor, firm_id: str) -> None:
    cur.execute(
        "INSERT INTO firms (id, name, clerk_org_id, created_at, updated_at)"
        " VALUES (%s, %s, %s, now(), now())",
        (firm_id, "Test Firm", f"clerk_{firm_id[:8]}"),
    )


def _insert_user(cur: psycopg2.extras.RealDictCursor, user_id: str, firm_id: str) -> None:
    cur.execute(
        "INSERT INTO users (id, firm_id, clerk_user_id, role, email, created_at, updated_at)"
        " VALUES (%s, %s, %s, 'PARALEGAL', %s, now(), now())",
        (user_id, firm_id, f"clerk_{user_id[:8]}", f"{user_id[:8]}@test.com"),
    )


def _insert_case(
    cur: psycopg2.extras.RealDictCursor,
    case_id: str,
    firm_id: str,
    user_id: str,
    status: str = "PROCESSING",
) -> None:
    cur.execute(
        "INSERT INTO cases"
        " (id, firm_id, created_by, claimant_name, dob, status, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s::case_status, now(), now())",
        (case_id, firm_id, user_id, b"encrypted_name", b"encrypted_dob", status),
    )


def _seed_case(conn) -> tuple[str, str, str]:
    """Insert firm + user + case; return (firm_id, user_id, case_id)."""
    firm_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        _insert_firm(cur, firm_id)
        _insert_user(cur, user_id, firm_id)
        _insert_case(cur, case_id, firm_id, user_id)
    return firm_id, user_id, case_id


def _insert_payer_token(
    conn,
    case_id: str,
    payer_slug: str = "bluebutton",
    expires_at: datetime | None = None,
) -> str:
    """Insert a minimal payer_token row; return its id."""
    token_id = str(uuid.uuid4())
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(hours=1)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO payer_tokens"
            " (id, case_id, payer_slug, access_token_enc, expires_at, created_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            (token_id, case_id, payer_slug, b"encrypted_access_token", expires_at),
        )
    return token_id


# ── payer_tokens tests ────────────────────────────────────────────────────────


def test_get_payer_token_found(conn):
    _, _, case_id = _seed_case(conn)
    token_id = _insert_payer_token(conn, case_id)

    row = get_payer_token(conn, case_id, "bluebutton")

    assert row is not None
    assert row["id"] == uuid.UUID(token_id) or str(row["id"]) == token_id
    assert row["case_id"] == uuid.UUID(case_id) or str(row["case_id"]) == case_id
    assert row["payer_slug"] == "bluebutton"
    assert bytes(row["access_token_enc"]) == b"encrypted_access_token"


def test_get_payer_token_not_found(conn):
    row = get_payer_token(conn, str(uuid.uuid4()), "bluebutton")
    assert row is None


def test_update_payer_token(conn):
    _, _, case_id = _seed_case(conn)
    token_id = _insert_payer_token(conn, case_id)
    new_expiry = datetime.now(UTC) + timedelta(hours=2)

    update_payer_token(conn, token_id, b"new_access_enc", b"new_refresh_enc", new_expiry)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT access_token_enc, refresh_token_enc FROM payer_tokens WHERE id = %s",
            (token_id,),
        )
        row = cur.fetchone()
    assert bytes(row["access_token_enc"]) == b"new_access_enc"
    assert bytes(row["refresh_token_enc"]) == b"new_refresh_enc"


def test_list_expiring_tokens_finds_expiring(conn):
    _, _, case_id = _seed_case(conn)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    _insert_payer_token(conn, case_id, expires_at=expires_at)

    results = list_expiring_tokens(conn, window_minutes=20)

    case_ids = [str(r["case_id"]) for r in results]
    assert case_id in case_ids


def test_list_expiring_tokens_skips_expired(conn):
    _, _, case_id = _seed_case(conn)
    expires_at = datetime.now(UTC) - timedelta(minutes=5)
    _insert_payer_token(conn, case_id, expires_at=expires_at)

    results = list_expiring_tokens(conn, window_minutes=20)

    case_ids = [str(r["case_id"]) for r in results]
    assert case_id not in case_ids


def test_list_expiring_tokens_skips_fresh(conn):
    _, _, case_id = _seed_case(conn)
    expires_at = datetime.now(UTC) + timedelta(minutes=60)
    _insert_payer_token(conn, case_id, expires_at=expires_at)

    results = list_expiring_tokens(conn, window_minutes=20)

    case_ids = [str(r["case_id"]) for r in results]
    assert case_id not in case_ids


# ── cases tests ───────────────────────────────────────────────────────────────


def test_get_case_found(conn):
    firm_id, user_id, case_id = _seed_case(conn)

    row = get_case(conn, case_id)

    assert row is not None
    assert str(row["id"]) == case_id
    assert str(row["firm_id"]) == firm_id
    assert row["status"] == "PROCESSING"


def test_get_case_not_found(conn):
    row = get_case(conn, str(uuid.uuid4()))
    assert row is None


def test_update_case_status(conn):
    _, _, case_id = _seed_case(conn)

    update_case_status(conn, case_id, "COMPLETE")

    with conn.cursor() as cur:
        cur.execute("SELECT status, updated_at FROM cases WHERE id = %s", (case_id,))
        row = cur.fetchone()
    assert row["status"] == "COMPLETE"
    assert row["updated_at"] is not None


# ── eob_raw tests ─────────────────────────────────────────────────────────────


def test_upsert_eob_raw_insert(conn):
    _, _, case_id = _seed_case(conn)

    upsert_eob_raw(conn, case_id, "fhir-eob-001", b"encrypted_eob_json")

    with conn.cursor() as cur:
        cur.execute("SELECT fhir_resource_id FROM eob_raw WHERE case_id = %s", (case_id,))
        row = cur.fetchone()
    assert row["fhir_resource_id"] == "fhir-eob-001"


def test_upsert_eob_raw_idempotent(conn):
    _, _, case_id = _seed_case(conn)

    upsert_eob_raw(conn, case_id, "fhir-eob-002", b"encrypted_v1")
    upsert_eob_raw(conn, case_id, "fhir-eob-002", b"encrypted_v2")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM eob_raw WHERE case_id = %s AND fhir_resource_id = %s",
            (case_id, "fhir-eob-002"),
        )
        row = cur.fetchone()
    assert row["cnt"] == 1


# ── providers tests ───────────────────────────────────────────────────────────


def test_upsert_provider_returns_id(conn):
    _, _, case_id = _seed_case(conn)

    provider_id = upsert_provider(
        conn, case_id, "1234567890", "Dr. Smith", "Cardiology", "123 Main St", "555-1234"
    )

    assert provider_id  # non-empty string
    # Should be parseable as UUID
    uuid.UUID(provider_id)


def test_upsert_provider_idempotent(conn):
    _, _, case_id = _seed_case(conn)

    id1 = upsert_provider(conn, case_id, "9876543210", "Dr. Jones", None, None, None)
    id2 = upsert_provider(conn, case_id, "9876543210", "Dr. Jones Updated", "Neurology", None, None)

    assert id1 == id2


# ── encounters tests ──────────────────────────────────────────────────────────


def test_insert_encounter(conn):
    _, _, case_id = _seed_case(conn)
    provider_id = upsert_provider(conn, case_id, "1111111111", "Dr. Test", None, None, None)

    insert_encounter(
        conn,
        case_id,
        provider_id,
        date(2024, 6, 15),
        ["Z00.00", "I10"],
        ["99213"],
        "City Hospital",
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT dx_codes, cpt_codes, facility_name FROM encounters WHERE case_id = %s",
            (case_id,),
        )
        row = cur.fetchone()
    assert row["dx_codes"] == ["Z00.00", "I10"]
    assert row["cpt_codes"] == ["99213"]
    assert row["facility_name"] == "City Hospital"


# ── prescriptions tests ───────────────────────────────────────────────────────


def test_insert_prescription(conn):
    _, _, case_id = _seed_case(conn)
    provider_id = upsert_provider(conn, case_id, "2222222222", "Dr. Rx", None, None, None)

    insert_prescription(
        conn,
        case_id,
        provider_id,
        "Metformin",
        "500mg",
        date(2024, 7, 1),
        "CVS Pharmacy",
        "3333333333",
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT drug_name, dosage, pharmacy_name FROM prescriptions WHERE case_id = %s",
            (case_id,),
        )
        row = cur.fetchone()
    assert row["drug_name"] == "Metformin"
    assert row["dosage"] == "500mg"
    assert row["pharmacy_name"] == "CVS Pharmacy"


# ── nppes_cache tests ─────────────────────────────────────────────────────────


def test_get_nppes_cache_miss(conn):
    row = get_nppes_cache(conn, "0000000000")
    assert row is None


def test_upsert_nppes_cache_and_get(conn):
    npi = "4444444444"
    data = {"name": "Dr. NPPES", "taxonomy": "207R00000X"}

    upsert_nppes_cache(conn, npi, data)
    row = get_nppes_cache(conn, npi)

    assert row is not None
    assert row["npi"] == npi
    assert row["data"]["name"] == "Dr. NPPES"


def test_upsert_nppes_cache_idempotent(conn):
    npi = "5555555555"

    upsert_nppes_cache(conn, npi, {"name": "v1"})
    upsert_nppes_cache(conn, npi, {"name": "v2"})
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM nppes_cache WHERE npi = %s",
            (npi,),
        )
        row = cur.fetchone()

    # Only one row despite two upserts
    assert row["cnt"] == 1
    # Data was updated
    row = get_nppes_cache(conn, npi)
    assert row is not None
    assert row["data"]["name"] == "v2"


# ── payer_health tests ────────────────────────────────────────────────────────


def test_update_payer_health_insert(conn):
    slug = f"test-payer-{uuid.uuid4().hex[:8]}"

    update_payer_health(conn, slug, "HEALTHY", 120, 0)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, consecutive_failures FROM payer_health WHERE payer_slug = %s",
            (slug,),
        )
        row = cur.fetchone()
    assert row["status"] == "HEALTHY"
    assert row["consecutive_failures"] == 0


def test_update_payer_health_increment(conn):
    slug = f"test-payer-{uuid.uuid4().hex[:8]}"

    update_payer_health(conn, slug, "DEGRADED", None, 1)
    update_payer_health(conn, slug, "DEGRADED", None, 1)

    with conn.cursor() as cur:
        cur.execute("SELECT consecutive_failures FROM payer_health WHERE payer_slug = %s", (slug,))
        row = cur.fetchone()
    assert row["consecutive_failures"] == 2


def test_update_payer_health_reset(conn):
    slug = f"test-payer-{uuid.uuid4().hex[:8]}"

    update_payer_health(conn, slug, "DOWN", None, 1)
    update_payer_health(conn, slug, "HEALTHY", 100, -1)

    with conn.cursor() as cur:
        cur.execute("SELECT consecutive_failures FROM payer_health WHERE payer_slug = %s", (slug,))
        row = cur.fetchone()
    assert row["consecutive_failures"] == 0


# ── audit_log tests ───────────────────────────────────────────────────────────


def test_insert_audit_log(conn):
    firm_id = str(uuid.uuid4())

    insert_audit_log(
        conn,
        action="EOB_PULL",
        resource_type="case",
        resource_id="some-case-id",
        firm_id=firm_id,
        metadata={"payer": "bluebutton", "eob_count": 42},
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT action, resource_type, resource_id, firm_id, metadata FROM audit_log"
            " WHERE firm_id = %s",
            (firm_id,),
        )
        row = cur.fetchone()
    assert row["action"] == "EOB_PULL"
    assert row["resource_type"] == "case"
    assert row["resource_id"] == "some-case-id"
    assert row["metadata"]["payer"] == "bluebutton"
    assert row["metadata"]["eob_count"] == 42
