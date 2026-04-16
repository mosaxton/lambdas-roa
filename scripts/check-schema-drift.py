#!/usr/bin/env python3
"""Contract test: verify Python SQL column names haven't drifted from Prisma schema.

Also checks that payer slugs and FHIR base URLs match between
lib/payers/registry.ts and shared/payer_registry.py.

Exit 0 = no drift detected.
Exit 1 = drift found (prints a report and fails CI).

Usage:
    python scripts/check-schema-drift.py
    python scripts/check-schema-drift.py --prisma vendor/app.rightofaccess/prisma/schema.prisma
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# ── Prisma schema parser ──────────────────────────────────────────────────────


def parse_prisma_tables(schema_path: Path) -> dict[str, list[str]]:
    """Return {table_name: [column_name, ...]} extracted from a Prisma schema.

    Uses @@map to get the SQL table name and @map/@db.* to get column names.
    Falls back to camelCase → snake_case conversion when no @map annotation.
    """
    text = schema_path.read_text()
    tables: dict[str, list[str]] = {}

    model_pattern = re.compile(r"model\s+(\w+)\s*\{([^}]+)\}", re.DOTALL)
    map_pattern = re.compile(r'@@map\("([^"]+)"\)')
    field_pattern = re.compile(r"^\s+(\w+)\s+\S", re.MULTILINE)
    field_map_pattern = re.compile(r'@map\("([^"]+)"\)')

    for model_match in model_pattern.finditer(text):
        model_name = model_match.group(1)
        body = model_match.group(2)

        # Determine SQL table name
        map_match = map_pattern.search(body)
        table_name = map_match.group(1) if map_match else _to_snake(model_name)

        columns: list[str] = []
        for field_match in field_pattern.finditer(body):
            field_name = field_match.group(1)
            # Skip Prisma directives
            if field_name in ("@@map", "@@id", "@@unique", "@@index"):
                continue
            # Check for @map annotation on the same line
            line = body[field_match.start() : body.find("\n", field_match.start())]
            col_map = field_map_pattern.search(line)
            sql_col = col_map.group(1) if col_map else _to_snake(field_name)
            columns.append(sql_col)

        tables[table_name] = columns

    return tables


def _to_snake(name: str) -> str:
    """Convert camelCase/PascalCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


# ── Python db.py column extractor ─────────────────────────────────────────────


def extract_python_columns(db_py_path: Path) -> str | None:
    """Return raw text of shared/db.py, or None if the file does not exist."""
    if not db_py_path.exists():
        return None
    return db_py_path.read_text()


# ── Payer registry checker ────────────────────────────────────────────────────


def check_payer_registry_drift() -> list[str]:
    """Compare payer slugs + FHIR URLs between TS and Python registries."""
    errors: list[str] = []

    ts_registry = REPO_ROOT / "vendor" / "app.rightofaccess" / "lib" / "payers" / "registry.ts"
    py_registry = REPO_ROOT / "shared" / "payer_registry.py"

    if not ts_registry.exists():
        errors.append(f"TS registry not found: {ts_registry}")
        return errors

    if not py_registry.exists():
        errors.append(
            f"Python payer registry not found: {py_registry}\n"
            f"  → Create shared/payer_registry.py with PAYERS dict matching lib/payers/registry.ts"
        )
        return errors

    ts_text = ts_registry.read_text()
    py_text = py_registry.read_text()

    # Extract slugs from TS: slug: "cms-blue-button"
    ts_slugs = set(re.findall(r'slug:\s*["\']([^"\']+)["\']', ts_text))
    py_slugs = set(re.findall(r'slug=["\']([^"\']+)["\']', py_text))

    if not ts_slugs:
        # Try alternative TS pattern: "cms-blue-button": {
        ts_slugs = set(re.findall(r'"([a-z][a-z0-9-]+)":\s*\{', ts_text))

    in_ts_not_py = ts_slugs - py_slugs
    in_py_not_ts = py_slugs - ts_slugs

    if in_ts_not_py:
        errors.append(
            f"Payer slug(s) in TS registry but missing from Python: {sorted(in_ts_not_py)}\n"
            f"  → Add them to shared/payer_registry.py"
        )
    if in_py_not_ts:
        errors.append(
            f"Payer slug(s) in Python registry but not in TS: {sorted(in_py_not_ts)}\n"
            f"  → Check lib/payers/registry.ts — may be stale in vendor submodule"
        )

    # Extract FHIR base URLs and compare
    # TS pattern:  fhirBaseUrl: "https://..."  or  baseUrl: "https://..."
    ts_urls = set(re.findall(r'(?:fhirBaseUrl|baseUrl):\s*["\']([^"\']+)["\']', ts_text))
    # Python pattern: fhir_base_url="https://..."  or  base_url="https://..."
    py_urls = set(re.findall(r'(?:fhir_base_url|base_url)=["\']([^"\']+)["\']', py_text))

    ts_urls_in_py = ts_urls - py_urls
    py_urls_not_in_ts = py_urls - ts_urls

    if ts_urls and py_urls:
        if ts_urls_in_py:
            errors.append(
                f"FHIR base URL(s) in TS registry but missing from Python: {sorted(ts_urls_in_py)}\n"
                f"  → Update shared/payer_registry.py base URLs"
            )
        if py_urls_not_in_ts:
            errors.append(
                f"FHIR base URL(s) in Python registry but not in TS: {sorted(py_urls_not_in_ts)}\n"
                f"  → Check lib/payers/registry.ts — may be stale"
            )

    return errors


# ── Column name sanity check against db.py ───────────────────────────────────

# Columns the plan declares as authoritative (section 3.2 of the plan).
# If these disappear from db.py, it's a bug.
REQUIRED_COLUMN_REFS: dict[str, list[str]] = {
    "cases": ["id", "firm_id", "status", "payer_slug", "updated_at"],
    "payer_tokens": [
        "id",
        "case_id",
        "payer_slug",
        "access_token_enc",
        "refresh_token_enc",
        "expires_at",
    ],
    "eob_raw": ["case_id", "fhir_resource_id", "raw_json_enc", "pulled_at"],
    "providers": ["case_id", "npi", "name", "specialty", "address", "phone"],
    "encounters": ["case_id", "provider_id", "date_of_service", "dx_codes", "cpt_codes"],
    "prescriptions": ["case_id", "provider_id", "drug_name", "fill_date"],
    "nppes_cache": ["npi", "data", "updated_at"],
    "payer_health": ["payer_slug", "status", "response_time_ms", "consecutive_failures"],
    "audit_log": ["action", "resource_type", "resource_id", "firm_id", "metadata", "timestamp"],
}


def check_db_column_drift(prisma_tables: dict[str, list[str]]) -> list[str]:
    """Check that the Prisma schema still has all columns referenced in REQUIRED_COLUMN_REFS."""
    errors: list[str] = []

    for table, required_cols in REQUIRED_COLUMN_REFS.items():
        prisma_cols = prisma_tables.get(table)
        if prisma_cols is None:
            errors.append(f"Table '{table}' not found in Prisma schema — was it renamed?")
            continue
        for col in required_cols:
            if col not in prisma_cols:
                errors.append(
                    f"Column '{col}' missing from Prisma table '{table}'.\n"
                    f"  → Either update db.py or fix the Prisma schema."
                )

    return errors


def check_python_column_refs(db_py_text: str) -> list[str]:
    """Verify that required column names appear somewhere in shared/db.py.

    This is a text-search sanity check, not a full SQL parser.  If a column
    from REQUIRED_COLUMN_REFS is absent it means the SQL helpers probably
    haven't been written for that table yet, or a column was renamed.
    """
    errors: list[str] = []
    for table, cols in REQUIRED_COLUMN_REFS.items():
        for col in cols:
            if col not in db_py_text:
                errors.append(
                    f"Column '{col}' (table '{table}') not referenced in shared/db.py.\n"
                    f"  → Add the column to the relevant SQL helper or update REQUIRED_COLUMN_REFS."
                )
    return errors


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Check for schema drift between Prisma and Python")
    parser.add_argument(
        "--prisma",
        default=str(REPO_ROOT / "vendor" / "app.rightofaccess" / "prisma" / "schema.prisma"),
        help="Path to Prisma schema file",
    )
    args = parser.parse_args()

    prisma_path = Path(args.prisma)
    errors: list[str] = []

    # 1. Parse Prisma schema
    if not prisma_path.exists():
        print(f"WARNING: Prisma schema not found at {prisma_path}", file=sys.stderr)
        print("         Run: git submodule update --init --recursive", file=sys.stderr)
        # Don't fail CI if the submodule isn't checked out — just warn
        return 0

    print(f"==> Parsing Prisma schema: {prisma_path}")
    prisma_tables = parse_prisma_tables(prisma_path)
    print(f"    Found tables: {sorted(prisma_tables.keys())}")

    # 2. Check required columns still exist in Prisma
    print("==> Checking column names against plan-defined requirements...")
    errors.extend(check_db_column_drift(prisma_tables))

    # 3. Check required column names appear in shared/db.py
    db_py_path = REPO_ROOT / "shared" / "db.py"
    db_py_text = extract_python_columns(db_py_path)
    if db_py_text is not None:
        print(f"==> Checking column references in {db_py_path}...")
        errors.extend(check_python_column_refs(db_py_text))
    else:
        print(f"    shared/db.py not found — skipping Python column reference check")

    # 5. Check payer registry sync
    print("==> Checking payer registry sync (TS ↔ Python)...")
    errors.extend(check_payer_registry_drift())

    # Report
    if errors:
        print("\n❌ Schema drift detected:\n", file=sys.stderr)
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}\n", file=sys.stderr)
        return 1

    print("✓ No schema drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
