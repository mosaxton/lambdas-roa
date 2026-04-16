"""Payer Health Check Lambda — EventBridge-scheduled FHIR metadata pinger."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from shared.audit import insert_audit_log
from shared.db import get_connection, update_payer_health
from shared.logging import get_logger, set_request_id
from shared.payer_registry import get_payer_config, list_payers
from shared.secrets import get_secret

logger = get_logger(os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "payer_health_check"))


def _post_slack_alert(slug: str, status: str, response_time_ms: int) -> None:
    webhook_url = get_secret(os.environ["SLACK_WEBHOOK_SECRET_NAME"])
    requests.post(
        webhook_url,
        json={
            "text": f":warning: Payer *{slug}* is {status} — "
            f"3 consecutive failures. Last response: {response_time_ms}ms."
        },
        timeout=5,
    )


def handler(event: dict[str, Any], context: Any) -> None:
    set_request_id(getattr(context, "aws_request_id", "local"))
    payers = list_payers()
    with get_connection() as conn:
        for slug in payers:
            config = get_payer_config(slug)
            url = f"{config.fhir_base_url}/metadata"
            start = time.monotonic()
            elapsed_ms = 10_000
            status = "DOWN"
            failures_delta = 1
            try:
                resp = requests.get(url, headers={"Accept": "application/fhir+json"}, timeout=10)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200 and elapsed_ms <= 3000:
                    status, failures_delta = "HEALTHY", 0
                elif resp.status_code == 200:
                    status, failures_delta = "DEGRADED", 0
                else:
                    status, failures_delta = "DOWN", 1
            except requests.Timeout:
                elapsed_ms = 10_000
                status, failures_delta = "DOWN", 1

            update_payer_health(conn, slug, status, elapsed_ms, failures_delta)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT consecutive_failures FROM payer_health WHERE payer_slug=%s",
                    (slug,),
                )
                row = cur.fetchone()
            if row and row["consecutive_failures"] >= 3:
                _post_slack_alert(slug, status, elapsed_ms)

            insert_audit_log(
                conn,
                action="HEALTH_CHECK",
                resource_type="payer",
                resource_id=slug,
                metadata={"status": status, "response_time_ms": elapsed_ms},
            )
