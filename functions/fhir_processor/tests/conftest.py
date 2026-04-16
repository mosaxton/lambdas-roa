"""Pytest configuration for fhir_processor tests.

Adds the functions/fhir_processor directory to sys.path so that
direct imports (import eob_parser, import fhir_client, etc.) work
identically to how Lambda imports them at runtime.
"""

import sys
from pathlib import Path

# Add functions/fhir_processor so direct module imports (import eob_parser) work,
# matching Lambda's runtime sys.path setup.
_fhir_processor_dir = str(Path(__file__).parent.parent)
if _fhir_processor_dir not in sys.path:
    sys.path.insert(0, _fhir_processor_dir)

# Add repo root so shared.* imports work when patching in tests.
_repo_root = str(Path(__file__).parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
