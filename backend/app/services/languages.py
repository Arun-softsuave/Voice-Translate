"""Which languages may be selected, and in which direction.

This is the single definition. The frontend used to carry a duplicate array
kept in sync by a comment; it now renders whatever `/health` reports.

The reason this needs a module at all is that `gpt-realtime-translate` is
**asymmetric**. It accepts 70+ source languages and detects the source
automatically, but will only *produce* a documented set of target languages.
Offering a target it cannot speak means the user finds out mid-call, after
paying for the call.

`gpt-realtime-2.1` has no such split — it is a general model told what to do in
a prompt — so in that mode both directions get the full list.

Note the labels are not only UI text: `realtime_service` interpolates them into
the model prompt ("Speak only {target}"), so they must stay natural English
language names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    label: str        # also used as prompt text — keep it a natural name
    native: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "label": self.label, "native": self.native}


_INDIAN = [
    Language("ta", "Tamil", "தமிழ்"),
    Language("hi", "Hindi", "हिन्दी"),
    Language("en", "English", "English"),
    Language("te", "Telugu", "తెలుగు"),
    Language("kn", "Kannada", "ಕನ್ನಡ"),
    Language("ml", "Malayalam", "മലയാളം"),
    Language("mr", "Marathi", "मराठी"),
    Language("bn", "Bengali", "বাংলা"),
    Language("gu", "Gujarati", "ગુજરાતી"),
]

_INTERNATIONAL = [
    Language("es", "Spanish", "Español"),
    Language("pt", "Portuguese", "Português"),
    Language("fr", "French", "Français"),
    Language("de", "German", "Deutsch"),
    Language("it", "Italian", "Italiano"),
    Language("ru", "Russian", "Русский"),
    Language("ja", "Japanese", "日本語"),
    Language("ko", "Korean", "한국어"),
    Language("zh", "Chinese", "中文"),
    Language("id", "Indonesian", "Bahasa Indonesia"),
    Language("vi", "Vietnamese", "Tiếng Việt"),
]

ALL: dict[str, Language] = {lang.code: lang for lang in _INDIAN + _INTERNATIONAL}

# The 13 targets OpenAI documents for gpt-realtime-translate.
DOCUMENTED_TRANSLATE_TARGETS = (
    "es", "pt", "fr", "ja", "ru", "zh", "de", "ko", "hi", "id", "vi", "it", "en",
)

# Tamil is NOT in OpenAI's documented list, but was verified working against the
# live API on 22 Sep 2026 — correct Tamil script and correct audio. It is kept
# because Hindi->Tamil is the direction this project exists to test. If it ever
# regresses, `scripts/probe_translate_model.py --to ta` will show it, and
# removing this tuple entry is the whole fix.
VERIFIED_UNDOCUMENTED_TARGETS = ("ta",)

TRANSLATE_TARGETS = DOCUMENTED_TRANSLATE_TARGETS + VERIFIED_UNDOCUMENTED_TARGETS

# The translate model detects the source itself and handles 70+ languages, so
# every language we know about is a valid source.
TRANSLATE_SOURCES = tuple(ALL)

# gpt-realtime-2.1 is a general model; it can work in either direction for
# anything we can name in the prompt.
REALTIME_TARGETS = tuple(ALL)
REALTIME_SOURCES = tuple(ALL)


def _ordered(codes) -> list[dict[str, str]]:
    """Keep the declaration order — Indian languages first, as the UI expects."""
    wanted = set(codes)
    return [ALL[c].as_dict() for c in ALL if c in wanted]


def sources_for(use_translate_backend: bool) -> list[dict[str, str]]:
    return _ordered(TRANSLATE_SOURCES if use_translate_backend else REALTIME_SOURCES)


def targets_for(use_translate_backend: bool) -> list[dict[str, str]]:
    return _ordered(TRANSLATE_TARGETS if use_translate_backend else REALTIME_TARGETS)


def source_codes(use_translate_backend: bool) -> tuple[str, ...]:
    return TRANSLATE_SOURCES if use_translate_backend else REALTIME_SOURCES


def target_codes(use_translate_backend: bool) -> tuple[str, ...]:
    return TRANSLATE_TARGETS if use_translate_backend else REALTIME_TARGETS


def name_of(code: str) -> str:
    """English name for a code, falling back to the code itself.

    The fallback is deliberate: an unknown code should degrade to showing "xx"
    rather than raising inside a live call.
    """
    lang = ALL.get(code)
    return lang.label if lang else code


# Backwards-compatible view for code that just wants code -> English name.
SUPPORTED_LANGUAGES: dict[str, str] = {c: l.label for c, l in ALL.items()}
