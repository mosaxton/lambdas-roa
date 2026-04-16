"""Pure function to convert a FHIR ExplanationOfBenefit resource into a normalized dict."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
EOB_TYPE_SYSTEM = "https://bluebutton.cms.gov/resources/codesystem/eob-type"
ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
HCPCS_SYSTEM = "https://bluebutton.cms.gov/resources/codesystem/hcpcs"
NDC_SYSTEM = "http://hl7.org/fhir/sid/ndc"
CARE_TEAM_ROLE_SYS = "http://hl7.org/fhir/us/carin-bb/CodeSystem/C4BBClaimCareTeamRole"

_KNOWN_ROLES = frozenset(
    {"billing", "performing", "prescribing", "facility", "attending", "operating"}
)


def _extract_claim_type(resource: dict[str, Any]) -> str:
    for coding in resource.get("type", {}).get("coding", []):
        if coding.get("system") == EOB_TYPE_SYSTEM:
            return str(coding.get("code", "UNKNOWN"))
    return "UNKNOWN"


def _extract_providers(resource: dict[str, Any]) -> list[dict[str, str]]:
    providers: list[dict[str, str]] = []
    for member in resource.get("careTeam", []):
        identifier = member.get("provider", {}).get("identifier")
        if identifier is None:
            continue
        identifiers = identifier if isinstance(identifier, list) else [identifier]
        npi: str | None = None
        for ident in identifiers:
            if ident.get("system") == NPI_SYSTEM:
                npi = str(ident.get("value", ""))
                break
        if not npi:
            continue
        role = "billing"
        for coding in member.get("role", {}).get("coding", []):
            if coding.get("code") in _KNOWN_ROLES:
                role = str(coding["code"])
                break
        providers.append({"npi": npi, "role": role})
    return providers


def _get_performing_npi(providers: list[dict[str, str]]) -> str | None:
    for p in providers:
        if p["role"] in ("performing", "attending", "operating"):
            return p["npi"]
    return providers[0]["npi"] if providers else None


def _extract_dx_codes(resource: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for dx in resource.get("diagnosis", []):
        for coding in dx.get("diagnosisCodeableConcept", {}).get("coding", []):
            if coding.get("system") == ICD10_SYSTEM and coding.get("code"):
                codes.append(str(coding["code"]))
    return codes


def _extract_cpt_codes(resource: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for item in resource.get("item", []):
        for coding in item.get("productOrService", {}).get("coding", []):
            if coding.get("system") == HCPCS_SYSTEM and coding.get("code"):
                codes.append(str(coding["code"]))
    return codes


def _extract_service_date(resource: dict[str, Any]) -> str | None:
    for item in resource.get("item", []):
        date = item.get("servicedDate")
        if date:
            return str(date)
        period_start = item.get("servicedPeriod", {}).get("start")
        if period_start:
            return str(period_start)
    period_start = resource.get("billablePeriod", {}).get("start")
    if period_start:
        return str(period_start)
    return None


def _extract_encounters(
    resource: dict[str, Any], providers: list[dict[str, str]], claim_type: str
) -> list[dict[str, Any]]:
    service_date = _extract_service_date(resource)
    if service_date is None:
        logger.warning("No service date found on EOB %s", resource.get("id"))
    provider_npi = _get_performing_npi(providers)
    if not provider_npi:
        return []
    facility_name: str | None = None
    if claim_type in ("INPATIENT", "OUTPATIENT"):
        facility_name = resource.get("facility", {}).get("display")
    return [
        {
            "provider_npi": provider_npi,
            "date_of_service": service_date,
            "dx_codes": _extract_dx_codes(resource),
            "cpt_codes": _extract_cpt_codes(resource),
            "facility_name": facility_name,
        }
    ]


def _extract_prescriptions(
    resource: dict[str, Any], providers: list[dict[str, str]]
) -> list[dict[str, Any]]:
    prescribing_npi: str | None = None
    for p in providers:
        if p["role"] == "prescribing":
            prescribing_npi = p["npi"]
            break

    prescriptions: list[dict[str, Any]] = []
    for item in resource.get("item", []):
        drug_name: str | None = None
        for coding in item.get("productOrService", {}).get("coding", []):
            if coding.get("system") == NDC_SYSTEM:
                drug_name = coding.get("display") or coding.get("code")
                break
        if not drug_name:
            continue
        fill_date = item.get("servicedDate") or item.get("servicedPeriod", {}).get("start")
        if fill_date is None:
            logger.warning("No fill date on PDE item in EOB %s", resource.get("id"))
        prescriptions.append(
            {
                "provider_npi": prescribing_npi,
                "drug_name": str(drug_name),
                "dosage": None,
                "fill_date": str(fill_date) if fill_date else None,
                "pharmacy_name": None,
                "pharmacy_npi": None,
            }
        )
    return prescriptions


def parse_eob(eob_resource: dict[str, Any]) -> dict[str, Any]:
    """Convert one FHIR ExplanationOfBenefit resource into a normalized dict."""
    claim_type = _extract_claim_type(eob_resource)
    providers = _extract_providers(eob_resource)

    if claim_type == "PDE":
        encounters: list[dict[str, Any]] = []
        prescriptions = _extract_prescriptions(eob_resource, providers)
    else:
        encounters = _extract_encounters(eob_resource, providers, claim_type)
        prescriptions = []

    return {
        "claim_type": claim_type,
        "providers": providers,
        "encounters": encounters,
        "prescriptions": prescriptions,
    }
