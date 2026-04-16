"""Cold Storage Mover — Phase 2, NOT active in MVP.

Moves raw EOB data for completed cases older than 12 months from RDS to S3.
Schedule is disabled until we have 12-month-old completed cases.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> None:
    logger.info("Cold storage mover triggered — Phase 2 not yet implemented. No-op.")
    # TODO Phase 2: query eob_raw for completed cases > 12 months old,
    # upload to s3://roa-cold-storage-prod/{case_id}/{fhir_resource_id}.enc,
    # delete from eob_raw after confirmed upload, write audit log.
