"""DLQ Alerter Lambda — marks cases ERROR and posts Slack on DLQ delivery."""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from shared.audit import insert_audit_log
from shared.db import get_case, get_connection, update_case_status
from shared.logging import get_logger, set_request_id
from shared.secrets import get_secret

logger = get_logger(os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "dlq_alerter"))


def _post_slack_alert(case_id: str, payer_slug: str) -> None:
    try:
        webhook_url = get_secret(os.environ["SLACK_WEBHOOK_SECRET_NAME"])
        requests.post(
            webhook_url,
            json={
                "text": f":red_circle: Case `{case_id}` failed FHIR processing "
                f"(payer: *{payer_slug}*) — sent to DLQ after 3 retries."
            },
            timeout=5,
        )
    except Exception:
        logger.error("Failed to post Slack alert for case", extra={"case_id": case_id})


def handler(event: dict[str, Any], context: Any) -> None:
    set_request_id(getattr(context, "aws_request_id", "local"))
    record = event["Records"][0]
    body = json.loads(record["body"])
    case_id: str | None = body.get("caseId")
    payer_slug: str = body.get("payerSlug", "unknown")

    if not case_id:
        logger.error("DLQ message missing caseId", extra={"body_keys": list(body.keys())})
        return

    with get_connection() as conn:
        case = get_case(conn, case_id)
        if not case:
            logger.warning("DLQ message for non-existent case", extra={"case_id": case_id})
            return

        update_case_status(conn, case_id, "ERROR")
        insert_audit_log(
            conn,
            action="EOB_PULL",
            resource_type="case",
            resource_id=case_id,
            firm_id=case.get("firm_id"),
            metadata={
                "result": "dlq",
                "payer_slug": payer_slug,
                "note": "max receive count exceeded",
            },
        )

    _post_slack_alert(case_id, payer_slug)
