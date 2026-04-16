"""FHIR Processor Lambda handler — SQS-triggered EOB pull, parse, and persist."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import eob_parser as eob_parser_mod
import fhir_client as fhir_client_mod
import nppes_resolver as nppes_resolver_mod
import requests

import shared.db as db
from shared.encryption import decrypt, encrypt
from shared.logging import get_logger, set_request_id
from shared.payer_registry import get_payer_config, list_payers

logger = get_logger("fhir_processor")

_PLACEHOLDER_PROVIDER = {"name": "", "specialty": "", "address": "", "phone": ""}


def _bridge_encryption_key() -> None:
    """Copy the encryption key from Secrets Manager into the env var encryption.py reads."""
    if not os.environ.get("ENCRYPTION_KEY"):
        from shared import secrets

        secret_name = os.environ.get("ENCRYPTION_KEY_SECRET_NAME", "")
        if secret_name:
            key = secrets.get_secret(secret_name)
            os.environ["ENCRYPTION_KEY"] = key


_bridge_encryption_key()


def _get_client_credentials() -> tuple[str, str]:
    from shared import secrets

    client_id = secrets.get_secret(os.environ["BB_CLIENT_ID_SECRET_NAME"])
    client_secret = secrets.get_secret(os.environ["BB_CLIENT_SECRET_SECRET_NAME"])
    return client_id, client_secret


def _refresh_token(
    token_row: dict[str, Any],
    payer_config: Any,
    case_id: str,
) -> str:
    """Attempt token refresh. Raises on failure."""
    refresh_token_enc: bytes | None = token_row.get("refresh_token_enc")
    if refresh_token_enc is None:
        raise ValueError("Token expired and no refresh token available")
    refresh_token_val = decrypt(bytes(refresh_token_enc))
    client_id, client_secret = _get_client_credentials()
    resp = requests.post(
        payer_config.token_url,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token_val},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    token_data = resp.json()
    new_access: str = str(token_data["access_token"])
    new_refresh = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in", 3600))
    new_expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in)

    with db.get_connection() as conn:
        db.update_payer_token(
            conn,
            token_row["id"],
            encrypt(new_access),
            encrypt(new_refresh) if new_refresh else None,
            new_expires_at,
        )
        db.insert_audit_log(
            conn,
            "TOKEN_REFRESH",
            "case",
            case_id,
            metadata={"payer_slug": token_row.get("payer_slug")},
        )
    return new_access


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS trigger. event['Records'] has exactly one record (BatchSize=1)."""
    set_request_id(getattr(context, "aws_request_id", "local"))
    start_ms = int(time.time() * 1000)

    record = event["Records"][0]
    body = json.loads(record["body"])
    case_id: str = body.get("caseId", "")
    payer_slug: str = body.get("payerSlug", "")

    logger.info("Processing SQS message", extra={"case_id": case_id, "payer_slug": payer_slug})

    # ── 1. Validate inputs ────────────────────────────────────────────────────
    try:
        uuid.UUID(case_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid case_id: {case_id!r}") from exc

    if payer_slug not in list_payers():
        try:
            with db.get_connection() as conn:
                db.update_case_status(conn, case_id, "ERROR")
                db.insert_audit_log(
                    conn, "EOB_PULL", "case", case_id, metadata={"error": "unknown_payer"}
                )
        except Exception:
            pass
        raise ValueError(f"Unknown payer slug: {payer_slug!r}")

    payer_config = get_payer_config(payer_slug)

    # ── 2. Fetch tokens (brief DB open, close before FHIR) ───────────────────
    with db.get_connection() as conn:
        token_row = db.get_payer_token(conn, case_id, payer_slug)
        case_row = db.get_case(conn, case_id)

    if token_row is None:
        with db.get_connection() as conn:
            db.update_case_status(conn, case_id, "ERROR")
        raise ValueError(f"No token row found for case {case_id}")

    firm_id: str | None = case_row.get("firm_id") if case_row else None

    # ── 3. Decrypt tokens and check freshness ────────────────────────────────
    try:
        access_token = decrypt(bytes(token_row["access_token_enc"]))
        patient_fhir_id_enc = token_row.get("patient_fhir_id_enc")
        patient_fhir_id = decrypt(bytes(patient_fhir_id_enc)) if patient_fhir_id_enc else ""
    except Exception as exc:
        with db.get_connection() as conn:
            db.update_case_status(conn, case_id, "ERROR")
        raise ValueError(f"Failed to decrypt token: {exc}") from exc

    expires_at: datetime = token_row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(tz=UTC):
        try:
            access_token = _refresh_token(token_row, payer_config, case_id)
        except Exception as exc:
            with db.get_connection() as conn:
                db.update_case_status(conn, case_id, "ERROR")
                db.insert_audit_log(
                    conn,
                    "EOB_PULL",
                    "case",
                    case_id,
                    firm_id=firm_id,
                    metadata={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:200],
                    },
                )
            raise

    # ── 4. FHIR fetch + accumulate ────────────────────────────────────────────
    all_providers: dict[str, dict[str, str]] = {}
    all_encounters: list[dict[str, Any]] = []
    all_prescriptions: list[dict[str, Any]] = []
    eob_count = 0

    try:
        for eob in fhir_client_mod.fetch_all_eobs(
            payer_config.fhir_base_url, patient_fhir_id, access_token
        ):
            eob_count += 1
            eob_id = str(eob.get("id", uuid.uuid4()))
            raw_json = json.dumps(eob)
            with db.get_connection() as conn:
                db.upsert_eob_raw(conn, case_id, eob_id, encrypt(raw_json))

            parsed = eob_parser_mod.parse_eob(eob)
            for provider in parsed["providers"]:
                npi = provider["npi"]
                if npi not in all_providers:
                    all_providers[npi] = provider
            all_encounters.extend(parsed["encounters"])
            all_prescriptions.extend(parsed["prescriptions"])
    except Exception as exc:
        logger.exception(
            "FHIR fetch failed", extra={"case_id": case_id, "error_type": type(exc).__name__}
        )
        with db.get_connection() as conn:
            db.update_case_status(conn, case_id, "ERROR")
            db.insert_audit_log(
                conn,
                "EOB_PULL",
                "case",
                case_id,
                firm_id=firm_id,
                metadata={"error_type": type(exc).__name__, "error_message": str(exc)[:200]},
            )
        raise

    # ── 5. Resolve NPIs and write providers, encounters, prescriptions ────────
    try:
        with db.get_connection() as conn:
            npi_to_provider_id: dict[str, str] = {}
            for npi in all_providers:
                nppes_data = nppes_resolver_mod.resolve_npi(npi, conn)
                if nppes_data is None:
                    nppes_data = {**_PLACEHOLDER_PROVIDER, "name": f"NPI {npi}"}
                provider_id = db.upsert_provider(
                    conn,
                    case_id,
                    npi,
                    nppes_data["name"],
                    nppes_data.get("specialty"),
                    nppes_data.get("address"),
                    nppes_data.get("phone"),
                )
                npi_to_provider_id[npi] = provider_id

            for enc in all_encounters:
                provider_npi = enc.get("provider_npi")
                if not provider_npi or provider_npi not in npi_to_provider_id:
                    continue
                db.insert_encounter(
                    conn,
                    case_id,
                    npi_to_provider_id[provider_npi],
                    enc["date_of_service"],
                    enc.get("dx_codes", []),
                    enc.get("cpt_codes", []),
                    enc.get("facility_name"),
                )

            for rx in all_prescriptions:
                provider_npi = rx.get("provider_npi")
                if not provider_npi or provider_npi not in npi_to_provider_id:
                    logger.warning("Skipping prescription with no resolvable prescriber NPI")
                    continue
                db.insert_prescription(
                    conn,
                    case_id,
                    npi_to_provider_id[provider_npi],
                    rx["drug_name"],
                    rx.get("dosage"),
                    rx["fill_date"],
                    rx.get("pharmacy_name"),
                    rx.get("pharmacy_npi"),
                )

            duration_ms = int(time.time() * 1000) - start_ms
            db.update_case_status(conn, case_id, "COMPLETE")
            db.insert_audit_log(
                conn,
                "EOB_PULL",
                "case",
                case_id,
                firm_id=firm_id,
                metadata={
                    "eob_count": eob_count,
                    "provider_count": len(npi_to_provider_id),
                    "encounter_count": len(all_encounters),
                    "prescription_count": len(all_prescriptions),
                    "duration_ms": duration_ms,
                    "payer_slug": payer_slug,
                },
            )

    except Exception as exc:
        logger.exception(
            "DB write failed", extra={"case_id": case_id, "error_type": type(exc).__name__}
        )
        with db.get_connection() as conn:
            db.update_case_status(conn, case_id, "ERROR")
            db.insert_audit_log(
                conn,
                "EOB_PULL",
                "case",
                case_id,
                firm_id=firm_id,
                metadata={"error_type": type(exc).__name__, "error_message": str(exc)[:200]},
            )
        raise

    logger.info(
        "Processing complete",
        extra={
            "case_id": case_id,
            "eob_count": eob_count,
            "provider_count": len(npi_to_provider_id),
        },
    )
    return {"batchItemFailures": []}
