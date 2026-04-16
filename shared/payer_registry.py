"""Python mirror of lib/payers/registry.ts — payer OAuth + FHIR config."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEFAULT_BB_BASE_URL = "https://sandbox.bluebutton.cms.gov"


@dataclass(frozen=True)
class PayerConfig:
    slug: str
    name: str
    authorization_url: str
    token_url: str
    fhir_base_url: str
    scopes: list[str] = field(default_factory=list)
    use_pkce: bool = False


def _build_payers() -> dict[str, PayerConfig]:
    bb_base_url = os.environ.get("BB_BASE_URL", _DEFAULT_BB_BASE_URL)
    return {
        "cms-blue-button": PayerConfig(
            slug="cms-blue-button",
            name="CMS Medicare (Blue Button)",
            authorization_url=f"{bb_base_url}/v2/o/authorize",
            token_url=f"{bb_base_url}/v2/o/token",
            fhir_base_url=f"{bb_base_url}/v2/fhir",
            scopes=[
                "patient/ExplanationOfBenefit.read",
                "patient/Patient.read",
                "patient/Coverage.read",
                "profile",
            ],
            use_pkce=True,
        )
    }


def get_payer_config(slug: str) -> PayerConfig:
    """Raises KeyError if slug not found."""
    payers = _build_payers()
    if slug not in payers:
        logger.error("Unknown payer slug: %r", slug)
        raise KeyError(f"Unknown payer slug: {slug!r}")
    return payers[slug]


def list_payers() -> list[str]:
    """Returns all registered payer slugs."""
    return list(_build_payers().keys())
