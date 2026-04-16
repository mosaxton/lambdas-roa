"""Tests for shared.payer_registry."""

import pytest

from shared.payer_registry import PayerConfig, get_payer_config, list_payers


def test_get_cms_blue_button_config_returns_correct_fields():
    config = get_payer_config("cms-blue-button")
    assert isinstance(config, PayerConfig)
    assert config.slug == "cms-blue-button"
    assert config.use_pkce is True
    assert "patient/ExplanationOfBenefit.read" in config.scopes
    assert "patient/Patient.read" in config.scopes
    assert "patient/Coverage.read" in config.scopes
    assert "profile" in config.scopes


def test_get_cms_blue_button_default_urls_use_sandbox():
    config = get_payer_config("cms-blue-button")
    assert "sandbox.bluebutton.cms.gov" in config.authorization_url
    assert "sandbox.bluebutton.cms.gov" in config.token_url
    assert "sandbox.bluebutton.cms.gov" in config.fhir_base_url


def test_get_payer_config_unknown_slug_raises_key_error():
    with pytest.raises(KeyError, match="Unknown payer slug"):
        get_payer_config("unknown-payer")


def test_list_payers_returns_all_slugs():
    slugs = list_payers()
    assert slugs == ["cms-blue-button"]


def test_urls_use_bb_base_url_env_var(monkeypatch):
    monkeypatch.setenv("BB_BASE_URL", "https://prod.bluebutton.cms.gov")
    config = get_payer_config("cms-blue-button")
    assert config.authorization_url == "https://prod.bluebutton.cms.gov/v2/o/authorize"
    assert config.token_url == "https://prod.bluebutton.cms.gov/v2/o/token"
    assert config.fhir_base_url == "https://prod.bluebutton.cms.gov/v2/fhir"


def test_urls_fall_back_to_sandbox_when_env_not_set(monkeypatch):
    monkeypatch.delenv("BB_BASE_URL", raising=False)
    config = get_payer_config("cms-blue-button")
    assert "sandbox.bluebutton.cms.gov" in config.fhir_base_url
