"""AWS Secrets Manager loader with per-container cache."""

from __future__ import annotations

import os
from typing import Any

_cache: dict[str, str] = {}
_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        import boto3  # noqa: PLC0415

        _client = boto3.client("secretsmanager")
    return _client


def get_secret(name: str) -> str:
    """Fetch from Secrets Manager; cached per Lambda container lifetime."""
    if name in _cache:
        return _cache[name]
    try:
        response = _get_client().get_secret_value(SecretId=name)
        value: str = response["SecretString"]
    except Exception as exc:
        if type(exc).__name__ in ("NoCredentialsError", "EndpointResolutionError"):
            value = os.environ.get(name, "")
        else:
            raise
    _cache[name] = value
    return value


def get_db_url() -> str:
    """Convenience: get_secret(DATABASE_URL_SECRET_NAME)."""
    return get_secret(os.environ["DATABASE_URL_SECRET_NAME"])


def get_encryption_key() -> str:
    """Convenience: get_secret(ENCRYPTION_KEY_SECRET_NAME)."""
    return get_secret(os.environ["ENCRYPTION_KEY_SECRET_NAME"])
