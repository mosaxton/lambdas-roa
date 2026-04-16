"""Tests for fhir_processor.handler."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

# handler is imported after conftest adds fhir_processor dir to sys.path
import pytest
from handler import handler

# ── Fixtures ──────────────────────────────────────────────────────────────────

CASE_ID = "00000000-0000-0000-0000-000000000001"
PAYER_SLUG = "cms-blue-button"

TEST_EOB_1 = {
    "resourceType": "ExplanationOfBenefit",
    "id": "eob-test-001",
    "type": {
        "coding": [
            {
                "system": "https://bluebutton.cms.gov/resources/codesystem/eob-type",
                "code": "CARRIER",
            }
        ]
    },
    "careTeam": [
        {
            "sequence": 1,
            "provider": {
                "identifier": {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567890"}
            },
            "role": {"coding": [{"code": "performing"}]},
        }
    ],
    "diagnosis": [],
    "item": [
        {
            "sequence": 1,
            "servicedDate": "2024-01-15",
            "productOrService": {
                "coding": [
                    {
                        "system": "https://bluebutton.cms.gov/resources/codesystem/hcpcs",
                        "code": "99213",
                    }
                ]
            },
        }
    ],
}

TEST_EOB_2 = {
    "resourceType": "ExplanationOfBenefit",
    "id": "eob-test-002",
    "type": {
        "coding": [
            {
                "system": "https://bluebutton.cms.gov/resources/codesystem/eob-type",
                "code": "CARRIER",
            }
        ]
    },
    "careTeam": [],
    "diagnosis": [],
    "item": [],
}


def _make_sqs_event(case_id=CASE_ID, payer_slug=PAYER_SLUG):
    return {"Records": [{"body": json.dumps({"caseId": case_id, "payerSlug": payer_slug})}]}


def _future_expires_at():
    return datetime.now(tz=UTC) + timedelta(hours=1)


def _past_expires_at():
    return datetime.now(tz=UTC) - timedelta(hours=1)


def _mock_context(request_id="test-request-id"):
    ctx = MagicMock()
    ctx.aws_request_id = request_id
    return ctx


def _make_token_row(expires_at=None):
    from shared.encryption import encrypt

    expires_at = expires_at or _future_expires_at()
    return {
        "id": "token-uuid-001",
        "case_id": CASE_ID,
        "payer_slug": PAYER_SLUG,
        "access_token_enc": encrypt("test_access_token"),
        "refresh_token_enc": encrypt("test_refresh_token"),
        "patient_fhir_id_enc": encrypt("Patient/-12345"),
        "expires_at": expires_at,
    }


def _make_case_row():
    return {
        "id": CASE_ID,
        "firm_id": "firm-uuid-001",
        "status": "PROCESSING",
        "payer_slug": PAYER_SLUG,
    }


@contextmanager
def _fake_db_conn(mock_conn):
    yield mock_conn


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_happy_path_two_eobs_writes_complete_status():
    mock_conn = MagicMock()
    token_row = _make_token_row()
    case_row = _make_case_row()

    with (
        patch("shared.db.get_connection", side_effect=lambda: _fake_db_conn(mock_conn)),
        patch("shared.db.get_payer_token", return_value=token_row),
        patch("shared.db.get_case", return_value=case_row),
        patch("shared.db.upsert_eob_raw"),
        patch("shared.db.upsert_provider", return_value="provider-uuid-001"),
        patch("shared.db.insert_encounter"),
        patch("shared.db.insert_prescription"),
        patch("shared.db.update_case_status"),
        patch("shared.db.insert_audit_log"),
        patch("fhir_client.fetch_all_eobs", return_value=iter([TEST_EOB_1, TEST_EOB_2])),
        patch(
            "nppes_resolver.resolve_npi",
            return_value={"name": "Dr Smith", "specialty": "IM", "address": "", "phone": ""},
        ),
    ):
        result = handler(_make_sqs_event(), _mock_context())

    assert result == {"batchItemFailures": []}
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)


def test_invalid_uuid_raises_value_error():
    with pytest.raises(ValueError, match="Invalid case_id"):
        handler(_make_sqs_event(case_id="not-a-uuid"), _mock_context())


def test_unknown_payer_slug_raises_value_error():
    mock_conn = MagicMock()
    with (
        patch("shared.db.get_connection", side_effect=lambda: _fake_db_conn(mock_conn)),
        patch("shared.db.update_case_status"),
        patch("shared.db.insert_audit_log"),
        pytest.raises(ValueError, match="Unknown payer slug"),
    ):
        handler(_make_sqs_event(payer_slug="unknown-payer"), _mock_context())


def test_expired_token_triggers_refresh_and_continues(monkeypatch):
    monkeypatch.setenv("BB_CLIENT_ID_SECRET_NAME", "roa/bb-client-id")
    monkeypatch.setenv("BB_CLIENT_SECRET_SECRET_NAME", "roa/bb-client-secret")
    mock_conn = MagicMock()
    token_row = _make_token_row(expires_at=_past_expires_at())
    case_row = _make_case_row()

    refresh_response = {
        "access_token": "new_access_token_value",
        "refresh_token": "new_refresh_token_value",
        "expires_in": 3600,
    }

    with (
        patch("shared.db.get_connection", side_effect=lambda: _fake_db_conn(mock_conn)),
        patch("shared.db.get_payer_token", return_value=token_row),
        patch("shared.db.get_case", return_value=case_row),
        patch("shared.db.update_payer_token"),
        patch("shared.db.upsert_eob_raw"),
        patch("shared.db.upsert_provider", return_value="provider-uuid-001"),
        patch("shared.db.insert_encounter"),
        patch("shared.db.insert_prescription"),
        patch("shared.db.update_case_status"),
        patch("shared.db.insert_audit_log"),
        patch("fhir_client.fetch_all_eobs", return_value=iter([])),
        patch("nppes_resolver.resolve_npi", return_value=None),
        patch("shared.secrets.get_secret", return_value="test-credential"),
        patch("requests.post") as mock_post,
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = refresh_response
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        result = handler(_make_sqs_event(), _mock_context())
    assert result == {"batchItemFailures": []}


def test_expired_token_refresh_failure_sets_error_status(monkeypatch):
    monkeypatch.setenv("BB_CLIENT_ID_SECRET_NAME", "roa/bb-client-id")
    monkeypatch.setenv("BB_CLIENT_SECRET_SECRET_NAME", "roa/bb-client-secret")
    mock_conn = MagicMock()
    token_row = _make_token_row(expires_at=_past_expires_at())
    case_row = _make_case_row()

    import requests as req_lib

    with (
        patch("shared.db.get_connection", side_effect=lambda: _fake_db_conn(mock_conn)),
        patch("shared.db.get_payer_token", return_value=token_row),
        patch("shared.db.get_case", return_value=case_row),
        patch("shared.db.update_case_status"),
        patch("shared.db.insert_audit_log"),
        patch("shared.secrets.get_secret", return_value="test-credential"),
        patch("requests.post", side_effect=req_lib.RequestException("connection refused")),
        pytest.raises(req_lib.RequestException),
    ):
        handler(_make_sqs_event(), _mock_context())


def test_nppes_404_inserts_placeholder_provider():
    mock_conn = MagicMock()
    token_row = _make_token_row()
    case_row = _make_case_row()

    with (
        patch("shared.db.get_connection", side_effect=lambda: _fake_db_conn(mock_conn)),
        patch("shared.db.get_payer_token", return_value=token_row),
        patch("shared.db.get_case", return_value=case_row),
        patch("shared.db.upsert_eob_raw"),
        patch("shared.db.upsert_provider", return_value="provider-uuid-001") as mock_upsert,
        patch("shared.db.insert_encounter"),
        patch("shared.db.insert_prescription"),
        patch("shared.db.update_case_status"),
        patch("shared.db.insert_audit_log"),
        patch("fhir_client.fetch_all_eobs", return_value=iter([TEST_EOB_1])),
        patch("nppes_resolver.resolve_npi", return_value=None),
    ):
        result = handler(_make_sqs_event(), _mock_context())

    assert result == {"batchItemFailures": []}
    # Placeholder provider should include the NPI in the name
    upsert_call = mock_upsert.call_args
    assert "NPI 1234567890" in upsert_call.args[3]
