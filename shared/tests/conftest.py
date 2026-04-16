"""
shared/tests/conftest.py — Testcontainers fixtures for db integration tests.

Tests call db.py helpers directly by passing the `conn` fixture; they do NOT
use get_connection(). The per-test `conn` fixture rolls back after each test
so every test starts with a clean slate.
"""

import os
from pathlib import Path


# Auto-detect Docker socket before testcontainers initialises.
# Supports Colima (macOS) and Docker Desktop. Ryuk/Reaper is disabled because
# Colima does not support bind-mounting the socket file into a container.
def _configure_testcontainers() -> None:
    if not os.environ.get("DOCKER_HOST"):
        colima_sock = Path.home() / ".colima" / "default" / "docker.sock"
        if colima_sock.exists():
            os.environ["DOCKER_HOST"] = f"unix://{colima_sock}"
    # Ryuk tries to mount the docker socket, which Colima rejects.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


_configure_testcontainers()

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
import pytest  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

# Ensure DATABASE_URL is set so any accidental get_connection() call
# doesn't crash with a missing-var error.
os.environ.setdefault("DATABASE_URL", "postgresql://roa:roa@localhost:5433/roa_lambdas_dev")

SCHEMA_SQL = (Path(__file__).parent.parent.parent / "scripts" / "schema.sql").read_text()


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(pg_container):
    url = pg_container.get_connection_url()
    # testcontainers may return postgresql+psycopg2:// — strip the driver suffix
    return url.replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="session")
def schema_conn(db_url):
    """Session-scoped connection that applies the schema once."""
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    cur.close()
    yield conn
    conn.close()


@pytest.fixture
def conn(schema_conn, db_url):
    """Per-test connection. Rolls back after each test for isolation."""
    c = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    c.autocommit = False
    yield c
    c.rollback()
    c.close()
