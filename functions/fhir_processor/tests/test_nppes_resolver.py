"""Tests for nppes_resolver.resolve_npi."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import responses as responses_lib
from nppes_resolver import resolve_npi

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
NPI = "1234567890"

_INDIVIDUAL_RESPONSE = {
    "result_count": 1,
    "results": [
        {
            "enumeration_type": "NPI-1",
            "basic": {
                "first_name": "John",
                "last_name": "Smith",
            },
            "taxonomies": [{"desc": "Internal Medicine"}],
            "addresses": [
                {
                    "address_1": "123 Main St",
                    "city": "Springfield",
                    "state": "IL",
                    "postal_code": "62701",
                    "telephone_number": "555-1234",
                }
            ],
        }
    ],
}

_ORG_RESPONSE = {
    "result_count": 1,
    "results": [
        {
            "enumeration_type": "NPI-2",
            "basic": {"organization_name": "Springfield Clinic"},
            "taxonomies": [{"desc": "Clinic/Center"}],
            "addresses": [
                {
                    "address_1": "456 Oak Ave",
                    "city": "Springfield",
                    "state": "IL",
                    "postal_code": "62702",
                    "telephone_number": "555-5678",
                }
            ],
        }
    ],
}

_NOW = datetime.now(tz=UTC)
_FRESH = _NOW - timedelta(hours=1)
_STALE = _NOW - timedelta(hours=200)


def _make_conn(cache_row=None):
    conn = MagicMock()
    return conn, cache_row


@responses_lib.activate
def test_cache_hit_within_ttl_returns_without_http_call():
    conn = MagicMock()
    with patch(
        "shared.db.get_nppes_cache",
        return_value={"updated_at": _FRESH, "data": _INDIVIDUAL_RESPONSE},
    ):
        result = resolve_npi(NPI, conn)
    assert result is not None
    assert result["name"] == "Smith, John"
    assert len(responses_lib.calls) == 0


@responses_lib.activate
def test_cache_miss_makes_http_call_and_caches():
    conn = MagicMock()
    responses_lib.add(responses_lib.GET, NPPES_URL, json=_INDIVIDUAL_RESPONSE, status=200)
    with (
        patch("shared.db.get_nppes_cache", return_value=None),
        patch("shared.db.upsert_nppes_cache") as mock_upsert,
    ):
        result = resolve_npi(NPI, conn)
    assert result is not None
    assert result["name"] == "Smith, John"
    assert result["specialty"] == "Internal Medicine"
    mock_upsert.assert_called_once()


@responses_lib.activate
def test_stale_cache_triggers_refresh():
    conn = MagicMock()
    responses_lib.add(responses_lib.GET, NPPES_URL, json=_INDIVIDUAL_RESPONSE, status=200)
    with (
        patch("shared.db.get_nppes_cache", return_value={"updated_at": _STALE, "data": {}}),
        patch("shared.db.upsert_nppes_cache") as mock_upsert,
    ):
        result = resolve_npi(NPI, conn)
    assert result is not None
    mock_upsert.assert_called_once()


@responses_lib.activate
def test_nppes_404_returns_none():
    conn = MagicMock()
    responses_lib.add(responses_lib.GET, NPPES_URL, status=404)
    with patch("shared.db.get_nppes_cache", return_value=None):
        result = resolve_npi(NPI, conn)
    assert result is None


@responses_lib.activate
def test_nppes_empty_results_returns_none():
    conn = MagicMock()
    responses_lib.add(
        responses_lib.GET, NPPES_URL, json={"result_count": 0, "results": []}, status=200
    )
    with patch("shared.db.get_nppes_cache", return_value=None):
        result = resolve_npi(NPI, conn)
    assert result is None


@responses_lib.activate
def test_organization_npi_uses_org_name():
    conn = MagicMock()
    responses_lib.add(responses_lib.GET, NPPES_URL, json=_ORG_RESPONSE, status=200)
    with (
        patch("shared.db.get_nppes_cache", return_value=None),
        patch("shared.db.upsert_nppes_cache"),
    ):
        result = resolve_npi(NPI, conn)
    assert result is not None
    assert result["name"] == "Springfield Clinic"


@responses_lib.activate
def test_individual_npi_uses_last_first_name():
    conn = MagicMock()
    responses_lib.add(responses_lib.GET, NPPES_URL, json=_INDIVIDUAL_RESPONSE, status=200)
    with (
        patch("shared.db.get_nppes_cache", return_value=None),
        patch("shared.db.upsert_nppes_cache"),
    ):
        result = resolve_npi(NPI, conn)
    assert result is not None
    assert result["name"] == "Smith, John"
