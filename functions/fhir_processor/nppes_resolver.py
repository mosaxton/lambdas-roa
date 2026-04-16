"""NPPES NPI registry resolver with RDS cache."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
_DEFAULT_TTL_HOURS = 168


def _nppes_url() -> str:
    return os.environ.get("NPPES_API_URL", _DEFAULT_NPPES_URL)


def _cache_ttl_hours() -> int:
    return int(os.environ.get("NPPES_CACHE_TTL_HOURS", str(_DEFAULT_TTL_HOURS)))


def _is_stale(updated_at: datetime) -> bool:
    cutoff = datetime.now(tz=UTC) - timedelta(hours=_cache_ttl_hours())
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at < cutoff


def _parse_nppes_response(data: dict[str, Any]) -> dict[str, Any] | None:
    results = data.get("results", [])
    if not results:
        return None
    rec = results[0]
    basic = rec.get("basic", {})

    entity_type = basic.get("enumeration_type") or rec.get("enumeration_type", "")
    if entity_type == "NPI-2" or basic.get("organization_name"):
        name = basic.get("organization_name", "")
    else:
        last = basic.get("last_name", "")
        first = basic.get("first_name", "")
        name = f"{last}, {first}".strip(", ")

    taxonomies = rec.get("taxonomies", [])
    specialty = taxonomies[0].get("desc", "") if taxonomies else ""

    addresses = rec.get("addresses", [])
    addr_parts: list[str] = []
    phone = ""
    if addresses:
        addr = addresses[0]
        for part in (
            addr.get("address_1"),
            addr.get("city"),
            addr.get("state"),
            addr.get("postal_code"),
        ):
            if part:
                addr_parts.append(str(part))
        phone = addr.get("telephone_number", "")

    return {
        "name": name,
        "specialty": specialty,
        "address": ", ".join(addr_parts),
        "phone": phone,
    }


def resolve_npi(npi: str, conn: Any) -> dict[str, Any] | None:
    """Check RDS cache first; if stale or missing, call NPPES API.

    Returns a dict with name, specialty, address, phone — or None if NPI not found.
    """
    from shared import db

    cached = db.get_nppes_cache(conn, npi)
    if cached is not None:
        updated_at: datetime = cached["updated_at"]
        if not _is_stale(updated_at):
            data = cached["data"]
            return _parse_nppes_response(data if isinstance(data, dict) else {})

    try:
        resp = requests.get(
            _nppes_url(),
            params={"number": npi, "version": "2.1"},
            timeout=10,
        )
    except requests.RequestException:
        logger.warning("NPPES request failed for NPI %s", npi)
        return None

    if resp.status_code == 404:
        return None

    try:
        resp.raise_for_status()
    except requests.HTTPError:
        logger.warning("NPPES returned HTTP %s for NPI %s", resp.status_code, npi)
        return None

    raw = resp.json()
    db.upsert_nppes_cache(conn, npi, raw)
    return _parse_nppes_response(raw)
