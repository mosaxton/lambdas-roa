"""Tests for shared.logging."""

import json
import logging

import pytest

import shared.logging as roa_logging


@pytest.fixture(autouse=True)
def reset_loggers():
    yield
    # Remove handlers added during tests to avoid bleed-over
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("roa."):
            log = logging.getLogger(name)
            log.handlers.clear()
            log.filters.clear()


def test_output_is_valid_json_with_required_fields(capsys):
    logger = roa_logging.get_logger("test_output_fn")
    logger.info("hello world")
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert "timestamp" in data
    assert data["function_name"] == "test_output_fn"
    assert "request_id" in data


def test_denylist_keys_are_redacted():
    data = {"access_token": "secret123", "user_id": "abc"}
    result = roa_logging.redact(data)
    assert result["access_token"] == "[REDACTED]"
    assert result["user_id"] == "abc"


def test_all_default_denylist_keys_are_redacted():
    phi_keys = [
        "refresh_token",
        "claimant_name",
        "dob",
        "ssn",
        "phone",
        "email",
        "raw_json",
        "pkce_verifier",
        "client_secret",
        "encryption_key",
    ]
    data = {k: "sensitive" for k in phi_keys}
    result = roa_logging.redact(data)
    for k in phi_keys:
        assert result[k] == "[REDACTED]", f"{k} should be redacted"


def test_caller_supplied_denylist_keys_are_also_redacted():
    data = {"my_custom_key": "sensitive", "normal_key": "ok"}
    result = roa_logging.redact(data, denylist={"my_custom_key"})
    assert result["my_custom_key"] == "[REDACTED]"
    assert result["normal_key"] == "ok"


def test_clean_keys_pass_through_unchanged():
    data = {"case_id": "abc-123", "payer_slug": "cms-blue-button", "count": 5}
    result = roa_logging.redact(data)
    assert result == {"case_id": "abc-123", "payer_slug": "cms-blue-button", "count": 5}


def test_redact_is_case_insensitive_on_denylist():
    data = {"ACCESS_TOKEN": "secret", "Email": "test@example.com", "SSN": "123-45-6789"}
    result = roa_logging.redact(data)
    assert result["ACCESS_TOKEN"] == "[REDACTED]"
    assert result["Email"] == "[REDACTED]"
    assert result["SSN"] == "[REDACTED]"


def test_get_logger_called_twice_does_not_duplicate_handlers():
    logger1 = roa_logging.get_logger("test_dedup_fn")
    logger2 = roa_logging.get_logger("test_dedup_fn")
    assert logger1 is logger2
    assert len(logger1.handlers) == 1


def test_set_request_id_appears_in_log_output(capsys):
    roa_logging.set_request_id("req-xyz-123")
    logger = roa_logging.get_logger("test_reqid_fn")
    logger.info("check request id")
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["request_id"] == "req-xyz-123"
