"""Language availability, split by direction.

The property that matters: the translate backend can *hear* far more languages
than it can *speak*. Offering a target it cannot produce means the failure
lands mid-call, after the call has been paid for.
"""

import pytest

from app.services import languages as L


# ------------------------------------------------------------- integrity
def test_every_code_in_every_set_exists():
    for name, codes in (
        ("TRANSLATE_SOURCES", L.TRANSLATE_SOURCES),
        ("TRANSLATE_TARGETS", L.TRANSLATE_TARGETS),
        ("REALTIME_SOURCES", L.REALTIME_SOURCES),
        ("REALTIME_TARGETS", L.REALTIME_TARGETS),
        ("GEMINI_SOURCES", L.GEMINI_SOURCES),
        ("GEMINI_TARGETS", L.GEMINI_TARGETS),
    ):
        unknown = [c for c in codes if c not in L.ALL]
        assert not unknown, f"{name} references codes missing from ALL: {unknown}"


def test_every_target_is_also_a_valid_source():
    """Otherwise a pair could be selected that can never be reversed."""
    for backend in ("translate", "realtime", "gemini"):
        sources = set(L.source_codes(backend))
        missing = [c for c in L.target_codes(backend) if c not in sources]
        assert not missing, f"{backend}: targets that are not sources: {missing}"


def test_no_duplicate_codes():
    assert len(L.TRANSLATE_TARGETS) == len(set(L.TRANSLATE_TARGETS))
    assert len(L.ALL) == len({lang.code for lang in L.ALL.values()})


def test_every_language_has_a_label_and_a_native_name():
    for code, lang in L.ALL.items():
        assert lang.label and lang.label[0].isupper(), code
        assert lang.native, code


# ------------------------------------------------------- the asymmetry
def test_translate_targets_are_narrower_than_its_sources():
    assert len(L.TRANSLATE_TARGETS) < len(L.TRANSLATE_SOURCES)


def test_realtime_mode_has_no_restriction_within_the_core_list():
    """The conversational model handles anything we can name in a prompt."""
    assert set(L.REALTIME_TARGETS) == set(L.CORE)
    assert set(L.REALTIME_SOURCES) == set(L.CORE)


def test_switching_mode_widens_the_target_list():
    assert len(L.targets_for("translate")) < len(L.targets_for("realtime"))


# -------------------------------------------------- the documented set
def test_all_thirteen_documented_targets_are_offered():
    assert len(L.DOCUMENTED_TRANSLATE_TARGETS) == 13
    for code in L.DOCUMENTED_TRANSLATE_TARGETS:
        assert code in L.TRANSLATE_TARGETS


def test_hindi_and_english_are_targets_in_both_modes():
    for code in ("hi", "en"):
        for backend in ("translate", "realtime", "gemini"):
            assert code in L.target_codes(backend), (backend, code)


def test_tamil_is_carried_as_verified_but_undocumented():
    """Tamil is not on OpenAI's list; we keep it because we tested it.

    If this ever has to be removed, it is one entry in
    VERIFIED_UNDOCUMENTED_TARGETS — this test is the reminder of why it exists.
    """
    assert "ta" not in L.DOCUMENTED_TRANSLATE_TARGETS
    assert "ta" in L.VERIFIED_UNDOCUMENTED_TARGETS
    assert "ta" in L.TRANSLATE_TARGETS


@pytest.mark.parametrize("code", ["te", "kn", "ml", "mr", "bn", "gu"])
def test_untested_indian_languages_are_sources_only(code):
    """They can be spoken, but we have not verified the model can produce them."""
    assert code in L.source_codes("translate")
    assert code not in L.target_codes("translate")


# ------------------------------------------------------------ rendering
def test_lists_are_dicts_the_ui_can_render_directly():
    for entry in L.targets_for("translate") + L.targets_for("gemini"):
        assert set(entry) == {"code", "label", "native"}


def test_indian_languages_come_first():
    """The UI shows the languages this project is actually about at the top."""
    for backend in ("translate", "realtime", "gemini"):
        labels = [e["label"] for e in L.targets_for(backend)]
        assert labels[:3] == ["Tamil", "Hindi", "English"], backend


def test_name_of_falls_back_to_the_code():
    """An unknown code must degrade, not raise, inside a live call."""
    assert L.name_of("ta") == "Tamil"
    assert L.name_of("zzz") == "zzz"


# ---------------------------------------------------------------- gemini
# Every language ai.google.dev lists for gemini-3.5-live-translate, with the
# zh-Hans/zh-Hant and pt-BR/pt-PT variants collapsed to our bare codes.
GEMINI_DOCUMENTED = (
    "af ak sq am ar hy az eu be bn bg my ca zh hr cs da nl en et fil fi fr gl "
    "ka de el gu ha he hi hu is id it ja jv kn kk km rw ko lo lv lt mk ms ml "
    "mr mn ne no fa pl pt pa ro ru sr sd si sk sl es su sw sv ta te th tr uk "
    "ur uz vi zu"
).split()


def test_gemini_offers_exactly_the_documented_languages():
    assert set(L.GEMINI_TARGETS) == set(GEMINI_DOCUMENTED)
    assert set(L.GEMINI_SOURCES) == set(GEMINI_DOCUMENTED)


@pytest.mark.parametrize("code", ["ta", "te", "kn", "ml", "mr", "bn", "gu", "hi",
                                  "pa", "ur"])
def test_gemini_can_speak_every_indian_language(code):
    """The reason to run Gemini at all: these are targets there, not just sources."""
    assert code in L.target_codes("gemini")


def test_gemini_is_the_widest_backend():
    assert len(L.targets_for("gemini")) > len(L.targets_for("realtime"))


def test_openai_backends_are_not_offered_the_gemini_only_languages():
    """Nothing outside the core list has been checked against OpenAI's models."""
    for backend in ("translate", "realtime"):
        assert "pa" not in L.source_codes(backend)
        assert "sw" not in L.target_codes(backend)


def test_gemini_codes_carry_the_variant_google_expects():
    assert L.gemini_code("zh") == "zh-Hans"
    assert L.gemini_code("pt") == "pt-BR"
    assert L.gemini_code("ta") == "ta"


# ------------------------------------------------------------- /health
def test_health_serves_the_gemini_lists_in_gemini_mode(monkeypatch):
    from dataclasses import replace

    from app.api.routes import health
    from app.config import get_settings

    body = health.health(replace(get_settings(), translation_backend="gemini"))

    assert body["translate_mode"] == "gemini"
    targets = {e["code"] for e in body["languages"]["target"]}
    assert {"ta", "te", "kn", "ml", "sw"} <= targets
    assert len(body["languages"]["target"]) == len(L.ALL)
