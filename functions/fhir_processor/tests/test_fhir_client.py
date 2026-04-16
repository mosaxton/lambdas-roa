"""Tests for fhir_client.fetch_all_eobs."""

from unittest.mock import patch

import pytest
import responses as responses_lib
from fhir_client import fetch_all_eobs
from requests import HTTPError

FHIR_BASE = "https://sandbox.bluebutton.cms.gov/v2/fhir"
PATIENT_ID = "Patient/-20140000010000"
ACCESS_TOKEN = "test_access_token"  # noqa: S105 — not a real secret
PAGE1_URL = f"{FHIR_BASE}/ExplanationOfBenefit?patient={PATIENT_ID}&_count=50"
PAGE2_URL = f"{FHIR_BASE}/ExplanationOfBenefit?patient={PATIENT_ID}&_count=50&startIndex=50"

EOB_1 = {"resourceType": "ExplanationOfBenefit", "id": "eob-1"}
EOB_2 = {"resourceType": "ExplanationOfBenefit", "id": "eob-2"}
EOB_3 = {"resourceType": "ExplanationOfBenefit", "id": "eob-3"}


def _bundle(resources, next_url=None):
    links: list[dict[str, str]] = [{"relation": "self", "url": PAGE1_URL}]
    if next_url:
        links.append({"relation": "next", "url": next_url})
    return {
        "resourceType": "Bundle",
        "entry": [{"resource": r} for r in resources],
        "link": links,
    }


@responses_lib.activate
def test_single_page_bundle_yields_all_resources():
    responses_lib.add(
        responses_lib.GET,
        PAGE1_URL,
        json=_bundle([EOB_1, EOB_2]),
        status=200,
    )
    result = list(fetch_all_eobs(FHIR_BASE, PATIENT_ID, ACCESS_TOKEN))
    assert len(result) == 2
    assert result[0]["id"] == "eob-1"
    assert result[1]["id"] == "eob-2"


@responses_lib.activate
def test_two_page_bundle_fully_consumed():
    responses_lib.add(
        responses_lib.GET,
        PAGE1_URL,
        json=_bundle([EOB_1, EOB_2], next_url=PAGE2_URL),
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        PAGE2_URL,
        json=_bundle([EOB_3]),
        status=200,
    )
    result = list(fetch_all_eobs(FHIR_BASE, PATIENT_ID, ACCESS_TOKEN))
    assert len(result) == 3
    assert {r["id"] for r in result} == {"eob-1", "eob-2", "eob-3"}


@responses_lib.activate
def test_empty_bundle_yields_nothing():
    responses_lib.add(
        responses_lib.GET,
        PAGE1_URL,
        json={"resourceType": "Bundle", "entry": [], "link": []},
        status=200,
    )
    result = list(fetch_all_eobs(FHIR_BASE, PATIENT_ID, ACCESS_TOKEN))
    assert result == []


@responses_lib.activate
def test_429_with_retry_after_sleeps_and_retries():
    responses_lib.add(
        responses_lib.GET,
        PAGE1_URL,
        headers={"Retry-After": "2"},
        status=429,
    )
    responses_lib.add(
        responses_lib.GET,
        PAGE1_URL,
        json=_bundle([EOB_1]),
        status=200,
    )
    with patch("fhir_client.time.sleep") as mock_sleep:
        result = list(fetch_all_eobs(FHIR_BASE, PATIENT_ID, ACCESS_TOKEN))
    mock_sleep.assert_called_once_with(2)
    assert len(result) == 1


@responses_lib.activate
def test_503_exhausts_retries_and_raises():
    for _ in range(3):
        responses_lib.add(responses_lib.GET, PAGE1_URL, status=503)
    with patch("fhir_client.time.sleep"), pytest.raises(HTTPError):
        list(fetch_all_eobs(FHIR_BASE, PATIENT_ID, ACCESS_TOKEN))
