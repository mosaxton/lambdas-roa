"""Token Refresh Lambda — proactively refreshes expiring OAuth tokens every 15 min."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from shared.audit import insert_audit_log
from shared.db import get_connection, list_expiring_tokens, update_payer_token
from shared.encryption import decrypt, encrypt
from shared.logging import get_logger, set_request_id
from shared.payer_registry import get_payer_config
from shared.secrets import get_secret

logger = get_logger(os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "token_refresh"))


def _emit_revoked_metric() -> None:
    import boto3

    boto3.client("cloudwatch").put_metric_data(
        Namespace="ROA/TokenRefresh",
        MetricData=[
            {
                "MetricName": "RevokedRefreshToken",
                "Dimensions": [
                    {"Name": "Environment", "Value": os.environ.get("ENVIRONMENT", "dev")}
                ],
                "Value": 1.0,
                "Unit": "Count",
            }
        ],
    )


def handler(event: dict[str, Any], context: Any) -> None:
    set_request_id(getattr(context, "aws_request_id", "local"))
    with get_connection() as conn:
        tokens = list_expiring_tokens(conn, window_minutes=20)
        for token in tokens:
            config = get_payer_config(token["payer_slug"])
            client_id = get_secret(os.environ["BB_CLIENT_ID_SECRET_NAME"])
            client_secret = get_secret(os.environ["BB_CLIENT_SECRET_SECRET_NAME"])

            refresh_token_plain = decrypt(bytes(token["refresh_token_enc"]))

            try:
                resp = requests.post(
                    config.token_url,
                    data={"grant_type": "refresh_token", "refresh_token": refresh_token_plain},
                    auth=(client_id, client_secret),
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                new_access_enc = encrypt(data["access_token"])
                new_refresh_enc = encrypt(data.get("refresh_token", refresh_token_plain))
                new_expires_at = datetime.now(tz=UTC) + timedelta(seconds=data["expires_in"])

                update_payer_token(
                    conn, token["id"], new_access_enc, new_refresh_enc, new_expires_at
                )
                insert_audit_log(
                    conn,
                    action="TOKEN_REFRESH",
                    resource_type="payer_token",
                    resource_id=token["id"],
                    metadata={"payer_slug": token["payer_slug"], "result": "refreshed"},
                )

            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (400, 401):
                    logger.warning(
                        "Refresh token revoked",
                        extra={"token_id": token["id"], "payer_slug": token["payer_slug"]},
                    )
                    _emit_revoked_metric()
                    insert_audit_log(
                        conn,
                        action="TOKEN_REFRESH",
                        resource_type="payer_token",
                        resource_id=token["id"],
                        metadata={"payer_slug": token["payer_slug"], "result": "revoked"},
                    )
                else:
                    logger.error(
                        "Token refresh network error — will retry next cron run",
                        extra={"error": str(e)},
                    )
