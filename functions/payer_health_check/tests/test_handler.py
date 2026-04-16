"""Tests for payer_health_check handler."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import requests as real_requests

from functions.payer_health_check.handler import handler


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.aws_request_id = "test-request-id"
    return ctx


def _make_payer_config(slug: str = "cms-blue-button") -> MagicMock:
    config = MagicMock()
    config.fhir_base_url = "https://sandbox.bluebutton.cms.gov/v2/fhir"
    return config


@contextmanager
def _fake_db_conn(
    mock_conn: MagicMock, consecutive_failures: int = 0
) -> Generator[MagicMock, None, None]:
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"consecutive_failures": consecutive_failures}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    yield mock_conn


def test_healthy_payer() -> None:
    mock_conn = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with (
        patch(
            "functions.payer_health_check.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.payer_health_check.handler.update_payer_health") as mock_update,
        patch("functions.payer_health_check.handler.insert_audit_log") as mock_audit,
        patch("functions.payer_health_check.handler.list_payers", return_value=["cms-blue-button"]),
        patch(
            "functions.payer_health_check.handler.get_payer_config",
            return_value=_make_payer_config(),
        ),
        patch(
            "functions.payer_health_check.handler.get_secret",
            return_value="https://hooks.slack.com/test",
        ),
        patch("functions.payer_health_check.handler.requests") as mock_requests,
    ):
        mock_requests.get.return_value = mock_resp
        mock_requests.Timeout = real_requests.Timeout

        handler({}, _make_context())

        call_args = mock_update.call_args
        assert call_args.args[2] == "HEALTHY"
        assert call_args.args[4] == 0
        mock_requests.post.assert_not_called()
        mock_audit.assert_called_once()


def test_slow_payer_degraded() -> None:
    mock_conn = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    call_count = 0

    def slow_monotonic() -> float:
        nonlocal call_count
        call_count += 1
        return 0.0 if call_count == 1 else 4.0

    with (
        patch(
            "functions.payer_health_check.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.payer_health_check.handler.update_payer_health") as mock_update,
        patch("functions.payer_health_check.handler.insert_audit_log"),
        patch("functions.payer_health_check.handler.list_payers", return_value=["cms-blue-button"]),
        patch(
            "functions.payer_health_check.handler.get_payer_config",
            return_value=_make_payer_config(),
        ),
        patch("functions.payer_health_check.handler.get_secret"),
        patch("functions.payer_health_check.handler.requests") as mock_requests,
        patch("time.monotonic", slow_monotonic),
    ):
        mock_requests.get.return_value = mock_resp
        mock_requests.Timeout = real_requests.Timeout

        handler({}, _make_context())

        call_args = mock_update.call_args
        assert call_args.args[2] == "DEGRADED"
        assert call_args.args[4] == 0
        mock_requests.post.assert_not_called()


def test_down_payer_http_500() -> None:
    mock_conn = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with (
        patch(
            "functions.payer_health_check.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn, 1),
        ),
        patch("functions.payer_health_check.handler.update_payer_health") as mock_update,
        patch("functions.payer_health_check.handler.insert_audit_log"),
        patch("functions.payer_health_check.handler.list_payers", return_value=["cms-blue-button"]),
        patch(
            "functions.payer_health_check.handler.get_payer_config",
            return_value=_make_payer_config(),
        ),
        patch("functions.payer_health_check.handler.get_secret"),
        patch("functions.payer_health_check.handler.requests") as mock_requests,
    ):
        mock_requests.get.return_value = mock_resp
        mock_requests.Timeout = real_requests.Timeout

        handler({}, _make_context())

        call_args = mock_update.call_args
        assert call_args.args[2] == "DOWN"
        assert call_args.args[4] == 1
        mock_requests.post.assert_not_called()


def test_third_consecutive_failure_posts_slack() -> None:
    mock_conn = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with (
        patch(
            "functions.payer_health_check.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn, 3),
        ),
        patch("functions.payer_health_check.handler.update_payer_health"),
        patch("functions.payer_health_check.handler.insert_audit_log"),
        patch("functions.payer_health_check.handler.list_payers", return_value=["cms-blue-button"]),
        patch(
            "functions.payer_health_check.handler.get_payer_config",
            return_value=_make_payer_config(),
        ),
        patch(
            "functions.payer_health_check.handler.get_secret",
            return_value="https://hooks.slack.com/services/test",
        ),
        patch("functions.payer_health_check.handler.requests") as mock_requests,
        patch.dict("os.environ", {"SLACK_WEBHOOK_SECRET_NAME": "roa/slack-webhook-url"}),
    ):
        mock_requests.get.return_value = mock_resp
        mock_requests.Timeout = real_requests.Timeout

        handler({}, _make_context())

        mock_requests.post.assert_called_once()
        post_json = mock_requests.post.call_args.kwargs.get("json", {})
        assert "cms-blue-button" in post_json.get("text", "")


def test_timeout_sets_down() -> None:
    mock_conn = MagicMock()

    with (
        patch(
            "functions.payer_health_check.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.payer_health_check.handler.update_payer_health") as mock_update,
        patch("functions.payer_health_check.handler.insert_audit_log"),
        patch("functions.payer_health_check.handler.list_payers", return_value=["cms-blue-button"]),
        patch(
            "functions.payer_health_check.handler.get_payer_config",
            return_value=_make_payer_config(),
        ),
        patch("functions.payer_health_check.handler.get_secret"),
        patch("functions.payer_health_check.handler.requests") as mock_requests,
    ):
        mock_requests.get.side_effect = real_requests.Timeout()
        mock_requests.Timeout = real_requests.Timeout

        handler({}, _make_context())

        call_args = mock_update.call_args
        assert call_args.args[2] == "DOWN"
        assert call_args.args[3] == 10_000
        assert call_args.args[4] == 1


def test_audit_log_written_for_each_payer() -> None:
    mock_conn = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with (
        patch(
            "functions.payer_health_check.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.payer_health_check.handler.update_payer_health"),
        patch("functions.payer_health_check.handler.insert_audit_log") as mock_audit,
        patch(
            "functions.payer_health_check.handler.list_payers",
            return_value=["cms-blue-button", "another-payer"],
        ),
        patch(
            "functions.payer_health_check.handler.get_payer_config",
            return_value=_make_payer_config(),
        ),
        patch("functions.payer_health_check.handler.get_secret"),
        patch("functions.payer_health_check.handler.requests") as mock_requests,
    ):
        mock_requests.get.return_value = mock_resp
        mock_requests.Timeout = real_requests.Timeout

        handler({}, _make_context())

        assert mock_audit.call_count == 2
        for call in mock_audit.call_args_list:
            assert call.kwargs["action"] == "HEALTH_CHECK"
            assert call.kwargs["resource_type"] == "payer"
