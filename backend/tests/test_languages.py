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
    ):
        unknown = [c for c in codes if c not in L.ALL]
        assert not unknown, f"{name} references codes missing from ALL: {unknown}"


def test_every_target_is_also_a_valid_source():
    """Otherwise a pair could be selected that can never be reversed."""
    for translate in (True, False):
        sources = set(L.source_codes(translate))
        missing = [c for c in L.target_codes(translate) if c not in sources]
        assert not missing, f"targets that are not sources: {missing}"


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


def test_realtime_mode_has_no_restriction():
    """The conversational model handles anything we can name in a prompt."""
    assert set(L.REALTIME_TARGETS) == set(L.ALL)
    assert set(L.REALTIME_SOURCES) == set(L.ALL)


def test_switching_mode_widens_the_target_list():
    assert len(L.targets_for(True)) < len(L.targets_for(False))


# -------------------------------------------------- the documented set
def test_all_thirteen_documented_targets_are_offered():
    assert len(L.DOCUMENTED_TRANSLATE_TARGETS) == 13
    for code in L.DOCUMENTED_TRANSLATE_TARGETS:
        assert code in L.TRANSLATE_TARGETS


def test_hindi_and_english_are_targets_in_both_modes():
    for code in ("hi", "en"):
        assert code in L.target_codes(True), code
        assert code in L.target_codes(False), code


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
    assert code in L.source_codes(True)
    assert code not in L.target_codes(True)


# ------------------------------------------------------------ rendering
def test_lists_are_dicts_the_ui_can_render_directly():
    for entry in L.targets_for(True):
        assert set(entry) == {"code", "label", "native"}


def test_indian_languages_come_first():
    """The UI shows the languages this project is actually about at the top."""
    labels = [e["label"] for e in L.targets_for(True)]
    assert labels[:3] == ["Tamil", "Hindi", "English"]


def test_name_of_falls_back_to_the_code():
    """An unknown code must degrade, not raise, inside a live call."""
    assert L.name_of("ta") == "Tamil"
    assert L.name_of("zzz") == "zzz"
