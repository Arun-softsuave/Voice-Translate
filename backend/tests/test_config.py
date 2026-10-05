"""Backend selection from the environment.

Two regressions this guards against:

* An unrecognised backend name used to fall through to `realtime` silently,
  so a typo ran a model nobody chose. It must refuse to start instead.
* `OPENAI_TRANSLATE_MODE` is the pre-Gemini name. Existing .env files use it,
  so it must keep working when `TRANSLATION_BACKEND` is unset.
"""

import pytest

from app import config


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("TRANSLATION_BACKEND", raising=False)
    monkeypatch.delenv("OPENAI_TRANSLATE_MODE", raising=False)
    return monkeypatch


def test_default_is_openai_translate(env):
    assert config._backend() == "translate"


@pytest.mark.parametrize("value", ["translate", "realtime", "gemini", "GEMINI", " gemini "])
def test_every_backend_is_accepted(env, value):
    env.setenv("TRANSLATION_BACKEND", value)
    assert config._backend() == value.strip().lower()


def test_old_variable_name_still_works(env):
    env.setenv("OPENAI_TRANSLATE_MODE", "realtime")
    assert config._backend() == "realtime"


def test_new_variable_wins_over_the_old_one(env):
    env.setenv("OPENAI_TRANSLATE_MODE", "realtime")
    env.setenv("TRANSLATION_BACKEND", "gemini")
    assert config._backend() == "gemini"


@pytest.mark.parametrize("typo", ["gemni", "openai", "google"])
def test_unknown_backend_refuses_to_start(env, typo):
    env.setenv("TRANSLATION_BACKEND", typo)
    with pytest.raises(RuntimeError, match="TRANSLATION_BACKEND"):
        config._backend()


def test_gemini_model_default(env):
    env.setenv("TRANSLATION_BACKEND", "gemini")
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.gemini_translate_model == "gemini-3.5-live-translate-preview"
        assert settings.translation_model == "gemini-3.5-live-translate-preview"
    finally:
        env.delenv("TRANSLATION_BACKEND")
        config.get_settings.cache_clear()
