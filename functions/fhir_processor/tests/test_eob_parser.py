"""Tests for eob_parser.parse_eob."""

import json
from pathlib import Path
from typing import Any

from eob_parser import parse_eob

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_carrier_eob_providers_and_encounters():
    eob = load_fixture("carrier_eob.json")
    result = parse_eob(eob)

    assert result["claim_type"] == "CARRIER"
    npis = {p["npi"] for p in result["providers"]}
    assert "1234567890" in npis
    assert "0987654321" in npis

    assert len(result["encounters"]) == 1
    enc = result["encounters"][0]
    assert enc["provider_npi"] == "0987654321"  # performing provider
    assert enc["date_of_service"] == "2024-01-15"
    assert "M54.5" in enc["dx_codes"]
    assert "99213" in enc["cpt_codes"]
    assert enc["facility_name"] is None

    assert result["prescriptions"] == []


def test_pde_eob_prescription_extraction():
    eob = load_fixture("pde_eob.json")
    result = parse_eob(eob)

    assert result["claim_type"] == "PDE"
    assert result["encounters"] == []

    assert len(result["prescriptions"]) == 1
    rx = result["prescriptions"][0]
    assert rx["provider_npi"] == "1122334455"
    assert rx["drug_name"] == "LISINOPRIL 10MG TABLET"
    assert rx["fill_date"] == "2024-02-10"


def test_inpatient_eob_facility_name_and_providers():
    eob = load_fixture("inpatient_eob.json")
    result = parse_eob(eob)

    assert result["claim_type"] == "INPATIENT"
    npis = {p["npi"] for p in result["providers"]}
    assert "5566778899" in npis
    assert "9988776655" in npis

    assert len(result["encounters"]) == 1
    enc = result["encounters"][0]
    assert enc["facility_name"] == "General Hospital"
    assert "I21.0" in enc["dx_codes"]
    assert "Z87.891" in enc["dx_codes"]


def test_empty_care_team_returns_empty_providers_no_exception():
    eob = {
        "resourceType": "ExplanationOfBenefit",
        "id": "no-care-team",
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
    result = parse_eob(eob)
    assert result["providers"] == []
    assert result["encounters"] == []


def test_missing_npi_system_skips_care_team_entry():
    eob = {
        "resourceType": "ExplanationOfBenefit",
        "id": "wrong-system",
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
                    "identifier": {"system": "http://hl7.org/fhir/sid/tax", "value": "XX999"}
                },
                "role": {"coding": [{"code": "billing"}]},
            }
        ],
        "diagnosis": [],
        "item": [],
    }
    result = parse_eob(eob)
    assert result["providers"] == []


def test_bundle_entries_parse_as_individual_eobs():
    bundle = load_fixture("bundle_page.json")
    resources = [entry["resource"] for entry in bundle["entry"]]
    # First resource has a performing provider and one encounter
    result0 = parse_eob(resources[0])
    assert result0["claim_type"] == "CARRIER"
    assert len(result0["providers"]) == 1
    assert len(result0["encounters"]) == 1

    # Second resource has empty careTeam → no encounters
    result1 = parse_eob(resources[1])
    assert result1["providers"] == []
    assert result1["encounters"] == []
