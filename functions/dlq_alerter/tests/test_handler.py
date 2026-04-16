"""Tests for dlq_alerter handler."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from functions.dlq_alerter.handler import handler

CASE_ID = "00000000-0000-0000-0000-000000000001"
PAYER_SLUG = "cms-blue-button"

ENV_VARS = {
    "SLACK_WEBHOOK_SECRET_NAME": "roa/slack-webhook-url",
    "ENVIRONMENT": "dev",
}


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.aws_request_id = "test-request-id"
    return ctx


def _make_sqs_event(case_id: str | None = CASE_ID, payer_slug: str = PAYER_SLUG) -> dict[str, Any]:
    body: dict[str, Any] = {"payerSlug": payer_slug}
    if case_id is not None:
        body["caseId"] = case_id
    return {"Records": [{"body": json.dumps(body)}]}


def _make_case_row(case_id: str = CASE_ID) -> dict[str, Any]:
    return {
        "id": case_id,
        "firm_id": "firm-uuid-001",
        "status": "PROCESSING",
        "payer_slug": PAYER_SLUG,
    }


@contextmanager
def _fake_db_conn(mock_conn: MagicMock) -> Generator[MagicMock, None, None]:
    yield mock_conn


def test_normal_dlq_message_sets_error_and_posts_slack() -> None:
    mock_conn = MagicMock()
    case_row = _make_case_row()

    with (
        patch(
            "functions.dlq_alerter.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.dlq_alerter.handler.get_case", return_value=case_row),
        patch("functions.dlq_alerter.handler.update_case_status") as mock_update_status,
        patch("functions.dlq_alerter.handler.insert_audit_log") as mock_audit,
        patch(
            "functions.dlq_alerter.handler.get_secret",
            return_value="https://hooks.slack.com/services/test",
        ),
        patch("functions.dlq_alerter.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        handler(_make_sqs_event(), _make_context())

        mock_update_status.assert_called_once_with(mock_conn, CASE_ID, "ERROR")
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "EOB_PULL"
        assert mock_audit.call_args.kwargs["metadata"]["result"] == "dlq"
        mock_requests.post.assert_called_once()


def test_case_not_found_returns_without_error() -> None:
    mock_conn = MagicMock()

    with (
        patch(
            "functions.dlq_alerter.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.dlq_alerter.handler.get_case", return_value=None),
        patch("functions.dlq_alerter.handler.update_case_status") as mock_update_status,
        patch("functions.dlq_alerter.handler.insert_audit_log") as mock_audit,
        patch("functions.dlq_alerter.handler.get_secret"),
        patch("functions.dlq_alerter.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        handler(_make_sqs_event(), _make_context())

        mock_update_status.assert_not_called()
        mock_audit.assert_not_called()
        mock_requests.post.assert_not_called()


def test_missing_case_id_returns_without_error() -> None:
    with (
        patch("functions.dlq_alerter.handler.get_connection") as mock_get_conn,
        patch("functions.dlq_alerter.handler.get_secret"),
        patch("functions.dlq_alerter.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        handler(_make_sqs_event(case_id=None), _make_context())

        mock_get_conn.assert_not_called()
        mock_requests.post.assert_not_called()


def test_slack_post_failure_does_not_reraise() -> None:
    mock_conn = MagicMock()
    case_row = _make_case_row()

    with (
        patch(
            "functions.dlq_alerter.handler.get_connection",
            side_effect=lambda: _fake_db_conn(mock_conn),
        ),
        patch("functions.dlq_alerter.handler.get_case", return_value=case_row),
        patch("functions.dlq_alerter.handler.update_case_status"),
        patch("functions.dlq_alerter.handler.insert_audit_log"),
        patch(
            "functions.dlq_alerter.handler.get_secret",
            side_effect=Exception("Secrets Manager unavailable"),
        ),
        patch("functions.dlq_alerter.handler.requests") as mock_requests,
        patch.dict("os.environ", ENV_VARS),
    ):
        handler(_make_sqs_event(), _make_context())

        mock_requests.post.assert_not_called()
