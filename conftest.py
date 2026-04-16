"""Top-level pytest configuration and shared fixtures.

Fixtures here are available to all tests across shared/ and functions/.
"""

import os

import pytest

# ── Test encryption key ───────────────────────────────────────────────────────
# Same key used to generate shared/tests/fixtures/ts_encrypted.bin.
# Safe to hardcode — it encrypts only synthetic test data.
TEST_ENCRYPTION_KEY = "5c3d4a2b1f8e7d6c9b0a1234567890abcdef0123456789abcdef0123456789ab"


@pytest.fixture(autouse=True)
def encryption_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set ENCRYPTION_KEY for every test that doesn't override it explicitly."""
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)


@pytest.fixture
def db_url() -> str:
    """Return the test database URL.

    Falls back gracefully if DATABASE_URL is not set so unit tests
    (which mock the DB) still pass in environments without Postgres.
    """
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://roa:roa@localhost:5433/roa_lambdas_dev?sslmode=disable",
    )


@pytest.fixture
def sample_case_id() -> str:
    """A stable UUID for use in test fixtures."""
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def sample_payer_slug() -> str:
    return "cms-blue-button"
