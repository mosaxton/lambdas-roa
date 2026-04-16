"""Tests for token_refresh handler."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import requests as real_requests

from functions.token_refresh.handler import handler

TOKEN_ID = "token-uuid-001"
PAYER_SLUG = "cms-blue-button"

ENV_VARS = {
    "BB_CLIENT_ID_SECRET_NAME": "roa/bb-client-id",
    "BB_CLIENT_SECRET_SECRET_NAME": "roa/bb-client-secret",
    "ENVIRONMENT": "dev",
}


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.aws_request_id = "test-request-id"
    return ctx


def _make_token_row(expires_at: datetime | None = None) -> dict[str, Any]:
    from shared.encryption import encrypt

    expires_at = expires_at or datetime.now(tz=UTC) + timedelta(minutes=10)
    return {
        "id": TOKEN_ID,
        "case_id": "00000000-0000-0000-0000-000000000001",
        "payer_slug": PAYER_SLUG,
        "access_token_enc": encrypt("old_access_token"),
        "refresh_token_enc": encrypt("old_refresh_token"),
        "expires_at": expires_at,
    }


@contextmanager
def _fake_db_conn(mock_conn: MagicMock) -> Generator[MagicMock, None, None]:
    yield mock_conn


def _make_payer_config() -> MagicMock:
    config = MagicMock()
    config.token_url = "https://sandbox.bluebutton.cms.gov/v2/o/token"
    return config


def test_expiring_token_refreshed_successfully() -> None:
    mock_conn = MagicMock()
    token_row = _make_token_row()

    refresh_resp_data = {
        "access_token": "new_access_token",
        "refresh_token": "new_refresh_token",
        "expires_in": 3600,
    }

    with (
        patch(
            "functions.token_refresh.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.token_refresh.handler.list_expiring_tokens", return_value=[token_row]),
        patch(
            "functions.token_refresh.handler.get_payer_config", return_value=_make_payer_config()
        ),
        patch("functions.token_refresh.handler.get_secret", return_value="secret-value"),
        patch("functions.token_refresh.handler.update_payer_token") as mock_update,
        patch("functions.token_refresh.handler.insert_audit_log") as mock_audit,
        patch("functions.token_refresh.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = refresh_resp_data
        mock_resp.raise_for_status.return_value = None
        mock_requests.post.return_value = mock_resp
        mock_requests.HTTPError = real_requests.HTTPError

        handler({}, _make_context())

        mock_update.assert_called_once()
        assert mock_update.call_args.args[1] == TOKEN_ID
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["metadata"]["result"] == "refreshed"


def test_revoked_token_db_untouched_audit_written() -> None:
    mock_conn = MagicMock()
    token_row = _make_token_row()

    http_error = real_requests.HTTPError(response=MagicMock(status_code=400))

    with (
        patch(
            "functions.token_refresh.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.token_refresh.handler.list_expiring_tokens", return_value=[token_row]),
        patch(
            "functions.token_refresh.handler.get_payer_config", return_value=_make_payer_config()
        ),
        patch("functions.token_refresh.handler.get_secret", return_value="secret-value"),
        patch("functions.token_refresh.handler.update_payer_token") as mock_update,
        patch("functions.token_refresh.handler.insert_audit_log") as mock_audit,
        patch("functions.token_refresh.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = http_error
        mock_requests.post.return_value = mock_resp
        mock_requests.HTTPError = real_requests.HTTPError

        handler({}, _make_context())

        mock_update.assert_not_called()
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["metadata"]["result"] == "revoked"


def test_revoked_token_emits_cloudwatch_metric() -> None:
    mock_conn = MagicMock()
    token_row = _make_token_row()

    http_error = real_requests.HTTPError(response=MagicMock(status_code=401))
    mock_cw = MagicMock()

    with (
        patch(
            "functions.token_refresh.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.token_refresh.handler.list_expiring_tokens", return_value=[token_row]),
        patch(
            "functions.token_refresh.handler.get_payer_config", return_value=_make_payer_config()
        ),
        patch("functions.token_refresh.handler.get_secret", return_value="secret-value"),
        patch("functions.token_refresh.handler.update_payer_token"),
        patch("functions.token_refresh.handler.insert_audit_log"),
        patch("functions.token_refresh.handler.requests") as mock_requests,
        patch("boto3.client", return_value=mock_cw),
        patch.dict("os.environ", ENV_VARS),
    ):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = http_error
        mock_requests.post.return_value = mock_resp
        mock_requests.HTTPError = real_requests.HTTPError

        handler({}, _make_context())

        mock_cw.put_metric_data.assert_called_once()
        call_kwargs = mock_cw.put_metric_data.call_args.kwargs
        assert call_kwargs["Namespace"] == "ROA/TokenRefresh"
        assert call_kwargs["MetricData"][0]["MetricName"] == "RevokedRefreshToken"


def test_network_error_logs_and_continues() -> None:
    mock_conn = MagicMock()
    token_row = _make_token_row()

    server_error = real_requests.HTTPError(response=MagicMock(status_code=503))

    with (
        patch(
            "functions.token_refresh.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.token_refresh.handler.list_expiring_tokens", return_value=[token_row]),
        patch(
            "functions.token_refresh.handler.get_payer_config", return_value=_make_payer_config()
        ),
        patch("functions.token_refresh.handler.get_secret", return_value="secret-value"),
        patch("functions.token_refresh.handler.update_payer_token") as mock_update,
        patch("functions.token_refresh.handler.insert_audit_log") as mock_audit,
        patch("functions.token_refresh.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = server_error
        mock_requests.post.return_value = mock_resp
        mock_requests.HTTPError = real_requests.HTTPError

        handler({}, _make_context())

        mock_update.assert_not_called()
        mock_audit.assert_not_called()


def test_multiple_expiring_tokens_all_processed() -> None:
    mock_conn = MagicMock()
    from shared.encryption import encrypt

    token1 = _make_token_row()
    token2 = {
        **_make_token_row(),
        "id": "token-uuid-002",
        "refresh_token_enc": encrypt("old_refresh_token_2"),
    }

    refresh_resp_data = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
        "expires_in": 3600,
    }

    with (
        patch(
            "functions.token_refresh.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch(
            "functions.token_refresh.handler.list_expiring_tokens", return_value=[token1, token2]
        ),
        patch(
            "functions.token_refresh.handler.get_payer_config", return_value=_make_payer_config()
        ),
        patch("functions.token_refresh.handler.get_secret", return_value="secret-value"),
        patch("functions.token_refresh.handler.update_payer_token") as mock_update,
        patch("functions.token_refresh.handler.insert_audit_log") as mock_audit,
        patch("functions.token_refresh.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = refresh_resp_data
        mock_resp.raise_for_status.return_value = None
        mock_requests.post.return_value = mock_resp
        mock_requests.HTTPError = real_requests.HTTPError

        handler({}, _make_context())

        assert mock_update.call_count == 2
        assert mock_audit.call_count == 2
