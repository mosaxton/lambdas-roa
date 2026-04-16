"""Audit log convenience wrapper with action validation and PHI redaction."""

from __future__ import annotations

from typing import Any

import psycopg2.extensions

from shared import db
from shared.logging import redact

VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "VIEW",
        "CREATE",
        "UPDATE",
        "DELETE",
        "EXPORT",
        "EOB_PULL",
        "TOKEN_REFRESH",
        "HEALTH_CHECK",
    }
)


def insert_audit_log(
    conn: psycopg2.extensions.connection,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    firm_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Thin wrapper around db.insert_audit_log. Never logs PHI in metadata."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown audit action: {action!r}")
    safe_metadata = redact(metadata or {})
    db.insert_audit_log(conn, action, resource_type, resource_id, firm_id, safe_metadata)
