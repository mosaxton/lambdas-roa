"""Tests for shared.secrets."""

from unittest.mock import MagicMock, patch

import pytest

import shared.secrets as secrets_module


@pytest.fixture(autouse=True)
def clear_secrets_cache():
    secrets_module._cache.clear()
    secrets_module._client = None
    yield
    secrets_module._cache.clear()
    secrets_module._client = None


def test_get_secret_calls_secrets_manager():
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "mysecretvalue"}
    with patch.object(secrets_module, "_get_client", return_value=mock_client):
        result = secrets_module.get_secret("roa/test-secret")
    assert result == "mysecretvalue"
    mock_client.get_secret_value.assert_called_once_with(SecretId="roa/test-secret")


def test_get_secret_caches_result_on_second_call():
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "cached_value"}
    with patch.object(secrets_module, "_get_client", return_value=mock_client):
        first = secrets_module.get_secret("roa/test-secret")
        second = secrets_module.get_secret("roa/test-secret")
    assert first == second == "cached_value"
    assert mock_client.get_secret_value.call_count == 1


def test_get_secret_local_fallback_on_no_credentials_error(monkeypatch):
    monkeypatch.setenv("my-test-secret", "local_value")

    class NoCredentialsError(Exception):
        pass

    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = NoCredentialsError("no creds")
    with patch.object(secrets_module, "_get_client", return_value=mock_client):
        result = secrets_module.get_secret("my-test-secret")
    assert result == "local_value"


def test_get_secret_local_fallback_on_endpoint_resolution_error(monkeypatch):
    monkeypatch.setenv("my-secret", "fallback")

    class EndpointResolutionError(Exception):
        pass

    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = EndpointResolutionError("no endpoint")
    with patch.object(secrets_module, "_get_client", return_value=mock_client):
        result = secrets_module.get_secret("my-secret")
    assert result == "fallback"


def test_get_secret_reraises_other_exceptions():
    class SomeOtherError(Exception):
        pass

    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = SomeOtherError("boom")
    with (
        patch.object(secrets_module, "_get_client", return_value=mock_client),
        pytest.raises(SomeOtherError),
    ):
        secrets_module.get_secret("roa/some-secret")


def test_get_db_url_uses_secret_name_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_SECRET_NAME", "roa/database-url")
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "postgresql://roa:roa@db/roa"}
    with patch.object(secrets_module, "_get_client", return_value=mock_client):
        result = secrets_module.get_db_url()
    assert result == "postgresql://roa:roa@db/roa"
    mock_client.get_secret_value.assert_called_once_with(SecretId="roa/database-url")


def test_get_encryption_key_uses_secret_name_from_env(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY_SECRET_NAME", "roa/encryption-key")
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "abc123hexkey"}
    with patch.object(secrets_module, "_get_client", return_value=mock_client):
        result = secrets_module.get_encryption_key()
    assert result == "abc123hexkey"
