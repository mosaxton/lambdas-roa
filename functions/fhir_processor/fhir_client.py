"""FHIR API client that fetches all EOB resources following Bundle pagination."""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from typing import Any

import requests

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 2


def _backoff_sleep(attempt: int, retry_after: int | None) -> None:
    delay = retry_after if retry_after is not None else _BASE_BACKOFF_SECONDS * (2**attempt)
    logger.info("Rate limited or server error; sleeping %s seconds before retry", delay)
    time.sleep(delay)


def fetch_all_eobs(
    fhir_base: str,
    patient_id: str,
    access_token: str,
    page_size: int = 50,
) -> Generator[dict[str, Any], None, None]:
    """Yield individual FHIR ExplanationOfBenefit resources following Bundle pagination.

    Applies exponential backoff on 429/503. Raises HTTPError after max retries.
    Never logs the access_token.
    """
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/fhir+json",
        }
    )

    url: str | None = f"{fhir_base}/ExplanationOfBenefit?patient={patient_id}&_count={page_size}"

    while url:
        response = _fetch_page(session, url)
        bundle = response.json()

        for entry in bundle.get("entry", []):
            resource = entry.get("resource")
            if resource:
                yield resource

        url = None
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                url = str(link["url"])
                break


def _fetch_page(session: requests.Session, url: str) -> requests.Response:
    for attempt in range(_MAX_RETRIES):
        response = session.get(url, timeout=30)
        if response.status_code in (429, 503):
            if attempt < _MAX_RETRIES - 1:
                retry_after_header = response.headers.get("Retry-After")
                retry_after = int(retry_after_header) if retry_after_header else None
                _backoff_sleep(attempt, retry_after)
                continue
            response.raise_for_status()
        elif response.status_code >= 400:
            response.raise_for_status()
        return response
    response.raise_for_status()
    return response  # unreachable but satisfies mypy
