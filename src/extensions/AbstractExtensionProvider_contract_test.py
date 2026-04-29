"""Unit tests for the provider-instance / provider-class contract surface.

Items 26 (AbstractProviderInstance), 27 (HealthReport / cached_health_check),
37 (Settings/EnvSchema), 10 (auth_strategy_name + build_auth_strategy),
17 (rate_limit / concurrency_limit ClassVars), 50 (environment_source).
Marked `unit` per AGENTS.md — pure-utility tests, no DB or extension load.
"""

from __future__ import annotations

import time
from typing import ClassVar, Optional, Type

import pytest
from pydantic import BaseModel

from extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtensionSystemComponent,
    AbstractStaticProvider,
    HealthReport,
    HealthStatus,
)


# ----- Item 26 -------------------------------------------------------------


@pytest.mark.unit
def test_abstract_provider_instance_constructor_accepts_optional_model():
    """`AbstractProviderInstance(...)` accepts a `ProviderInstanceModel`
    (or None for tests). The default `__init__` stores it on `.model`."""
    inst = AbstractProviderInstance()
    assert inst.model is None


@pytest.mark.unit
def test_abstract_provider_instance_validate_credentials_default_true():
    inst = AbstractProviderInstance()
    assert inst.validate_credentials() is True


@pytest.mark.unit
def test_abstract_provider_instance_close_default_noop():
    """close() must not raise on the default impl."""
    inst = AbstractProviderInstance()
    inst.close()  # no exception


@pytest.mark.unit
def test_abstract_static_provider_has_typed_instance_attribute():
    """`_instance: ClassVar[Optional[AbstractProviderInstance]]` on
    AbstractStaticProvider per Item 26."""
    assert hasattr(AbstractStaticProvider, "_instance")
    # Default class-level value is None; type is Optional[AbstractProviderInstance].
    assert AbstractStaticProvider._instance is None


# ----- Item 37 -------------------------------------------------------------


@pytest.mark.unit
def test_settings_envschema_classvars_default_none():
    """`Settings` and `EnvSchema` are declared as ClassVar[Optional[Type[BaseModel]]]
    with default None on the system-component base."""
    assert AbstractStaticExtensionSystemComponent.Settings is None
    assert AbstractStaticExtensionSystemComponent.EnvSchema is None


@pytest.mark.unit
def test_get_setting_consults_settings_then_env():
    """`get_setting` prefers `Settings` (Pydantic) when present,
    falls back to `_env` dict otherwise."""

    class _Settings(BaseModel):
        base_url: str = "https://api.example"

    class _Component(AbstractStaticExtensionSystemComponent):
        name = "sample_component"
        Settings = _Settings
        _env = {"timeout": "30"}

    # Pydantic-typed value wins for Settings-declared keys.
    assert _Component.get_setting("base_url") == "https://api.example"
    # Falls back to _env for keys not on Settings.
    assert _Component.get_setting("timeout") == "30"
    # Default returned when neither has the key.
    assert _Component.get_setting("nope", "fallback") == "fallback"


@pytest.mark.unit
def test_settings_must_inherit_basemodel():
    """Declaring a non-BaseModel as Settings raises TypeError at class
    definition time (Item 37 enforcement)."""
    with pytest.raises(TypeError, match="BaseModel"):

        class _Bad(AbstractStaticExtensionSystemComponent):
            name = "bad_component"
            Settings = object  # not a BaseModel


# ----- Item 27 -------------------------------------------------------------


@pytest.mark.unit
def test_healthreport_carries_status_timestamp_detail():
    rep = HealthReport(HealthStatus.OK, detail="all good")
    assert rep.status is HealthStatus.OK
    assert rep.detail == "all good"
    assert rep.timestamp is not None


@pytest.mark.unit
def test_health_check_default_reports_ok_when_configured(monkeypatch):
    class _Provider(AbstractStaticProvider):
        name = "sample_provider"
        _env = {}  # no required env -> is_configured == True

        @classmethod
        def bond_instance(cls, instance):  # type: ignore[override]
            return AbstractProviderInstance(instance)

        @classmethod
        def root(cls):  # type: ignore[override]
            return None

    rep = _Provider.health_check()
    assert rep.status is HealthStatus.OK


@pytest.mark.unit
def test_cached_health_check_returns_same_within_ttl(monkeypatch):
    class _Provider(AbstractStaticProvider):
        name = "sample_provider_cached"
        _env = {}

        @classmethod
        def bond_instance(cls, instance):  # type: ignore[override]
            return AbstractProviderInstance(instance)

        @classmethod
        def root(cls):  # type: ignore[override]
            return None

    _Provider._cached_health = None
    _Provider._cached_health_at = 0.0
    rep1 = _Provider.cached_health_check(ttl_seconds=60)
    rep2 = _Provider.cached_health_check(ttl_seconds=60)
    assert rep1 is rep2  # cached identity


# ----- Item 10 / Item 17 / Item 50 ----------------------------------------


@pytest.mark.unit
def test_provider_classvars_have_documented_defaults():
    """auth_strategy_name defaults to 'api_key', environment_source to APP_ENV,
    rate_limit / concurrency_limit / rotation_policy default None."""
    assert AbstractStaticProvider.auth_strategy_name == "api_key"
    assert AbstractStaticProvider.environment_source == "APP_ENV"
    assert AbstractStaticProvider.rate_limit is None
    assert AbstractStaticProvider.concurrency_limit is None
    assert AbstractStaticProvider.rotation_policy is None


@pytest.mark.unit
def test_build_auth_strategy_uses_class_default():
    from extensions.AuthStrategy import APIKeyAuth

    class _FakeInstance:
        # simulates ProviderInstanceModel without auth_strategy_name override
        pass

    class _Provider(AbstractStaticProvider):
        name = "p_api_key_default"
        auth_strategy_name = "api_key"

        @classmethod
        def bond_instance(cls, instance):  # type: ignore[override]
            return AbstractProviderInstance(instance)

        @classmethod
        def root(cls):  # type: ignore[override]
            return None

    strat = _Provider.build_auth_strategy(_FakeInstance(), {"api_key": "x"})
    assert isinstance(strat, APIKeyAuth)
    assert strat.headers_for() == {"Authorization": "Bearer x"}


@pytest.mark.unit
def test_build_auth_strategy_honors_per_instance_override():
    from extensions.AuthStrategy import JWTBearerAuth

    class _Instance:
        auth_strategy_name = "jwt_bearer"

    class _Provider(AbstractStaticProvider):
        name = "p_default_api_with_per_instance_jwt"
        auth_strategy_name = "api_key"

        @classmethod
        def bond_instance(cls, instance):  # type: ignore[override]
            return AbstractProviderInstance(instance)

        @classmethod
        def root(cls):  # type: ignore[override]
            return None

    strat = _Provider.build_auth_strategy(_Instance(), {"token": "abc"})
    assert isinstance(strat, JWTBearerAuth)
