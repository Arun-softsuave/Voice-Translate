"""Which languages may be selected, and in which direction.

This is the single definition. The frontend used to carry a duplicate array
kept in sync by a comment; it now renders whatever `/health` reports.

The reason this needs a module at all is that `gpt-realtime-translate` is
**asymmetric**. It accepts 70+ source languages and detects the source
automatically, but will only *produce* a documented set of target languages.
Offering a target it cannot speak means the user finds out mid-call, after
paying for the call.

`gpt-realtime-2.1` has no such split — it is a general model told what to do in
a prompt — so in that mode both directions get the full core list.

`gemini-3.5-live-translate` documents 70+ languages as BOTH source and target,
including every Indian language here. It is the only backend offered the
extended list; the OpenAI sets are deliberately left at the core list, because
nothing beyond it has been checked against those models.

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

# Documented for gemini-3.5-live-translate only. South Asian languages first,
# for the same reason _INDIAN leads the core list.
_GEMINI_ONLY = [
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    Language("ur", "Urdu", "اردو"),
    Language("ne", "Nepali", "नेपाली"),
    Language("si", "Sinhala", "සිංහල"),
    Language("sd", "Sindhi", "سنڌي"),
    Language("af", "Afrikaans", "Afrikaans"),
    Language("ak", "Akan", "Akan"),
    Language("sq", "Albanian", "Shqip"),
    Language("am", "Amharic", "አማርኛ"),
    Language("ar", "Arabic", "العربية"),
    Language("hy", "Armenian", "Հայերեն"),
    Language("az", "Azerbaijani", "Azərbaycan"),
    Language("eu", "Basque", "Euskara"),
    Language("be", "Belarusian", "Беларуская"),
    Language("bg", "Bulgarian", "Български"),
    Language("my", "Burmese", "မြန်မာ"),
    Language("ca", "Catalan", "Català"),
    Language("hr", "Croatian", "Hrvatski"),
    Language("cs", "Czech", "Čeština"),
    Language("da", "Danish", "Dansk"),
    Language("nl", "Dutch", "Nederlands"),
    Language("et", "Estonian", "Eesti"),
    Language("fil", "Filipino", "Filipino"),
    Language("fi", "Finnish", "Suomi"),
    Language("gl", "Galician", "Galego"),
    Language("ka", "Georgian", "ქართული"),
    Language("el", "Greek", "Ελληνικά"),
    Language("ha", "Hausa", "Hausa"),
    Language("he", "Hebrew", "עברית"),
    Language("hu", "Hungarian", "Magyar"),
    Language("is", "Icelandic", "Íslenska"),
    Language("jv", "Javanese", "Basa Jawa"),
    Language("kk", "Kazakh", "Қазақ"),
    Language("km", "Khmer", "ខ្មែរ"),
    Language("rw", "Kinyarwanda", "Ikinyarwanda"),
    Language("lo", "Lao", "ລາວ"),
    Language("lv", "Latvian", "Latviešu"),
    Language("lt", "Lithuanian", "Lietuvių"),
    Language("mk", "Macedonian", "Македонски"),
    Language("ms", "Malay", "Bahasa Melayu"),
    Language("mn", "Mongolian", "Монгол"),
    Language("no", "Norwegian", "Norsk"),
    Language("fa", "Persian", "فارسی"),
    Language("pl", "Polish", "Polski"),
    Language("ro", "Romanian", "Română"),
    Language("sr", "Serbian", "Српски"),
    Language("sk", "Slovak", "Slovenčina"),
    Language("sl", "Slovenian", "Slovenščina"),
    Language("su", "Sundanese", "Basa Sunda"),
    Language("sw", "Swahili", "Kiswahili"),
    Language("sv", "Swedish", "Svenska"),
    Language("th", "Thai", "ไทย"),
    Language("tr", "Turkish", "Türkçe"),
    Language("uk", "Ukrainian", "Українська"),
    Language("uz", "Uzbek", "Oʻzbek"),
    Language("zu", "Zulu", "isiZulu"),
]

CORE: tuple[str, ...] = tuple(lang.code for lang in _INDIAN + _INTERNATIONAL)

ALL: dict[str, Language] = {
    lang.code: lang for lang in _INDIAN + _INTERNATIONAL + _GEMINI_ONLY
}

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
# every core language is a valid source.
TRANSLATE_SOURCES = CORE

# gpt-realtime-2.1 is a general model; it can work in either direction for
# anything we can name in the prompt.
REALTIME_TARGETS = CORE
REALTIME_SOURCES = CORE

# Every language Google lists for gemini-3.5-live-translate, both directions.
GEMINI_TARGETS = tuple(ALL)
GEMINI_SOURCES = tuple(ALL)

# Gemini wants a script or region on these two; our codes are the bare ones
# the OpenAI backends and the UI already use.
GEMINI_CODES = {"zh": "zh-Hans", "pt": "pt-BR"}


def gemini_code(code: str) -> str:
    """The BCP-47 code Gemini expects for one of ours."""
    return GEMINI_CODES.get(code, code)


def _ordered(codes) -> list[dict[str, str]]:
    """Keep the declaration order — Indian languages first, as the UI expects."""
    wanted = set(codes)
    return [ALL[c].as_dict() for c in ALL if c in wanted]


_SOURCES = {
    "translate": TRANSLATE_SOURCES,
    "realtime": REALTIME_SOURCES,
    "gemini": GEMINI_SOURCES,
}
_TARGETS = {
    "translate": TRANSLATE_TARGETS,
    "realtime": REALTIME_TARGETS,
    "gemini": GEMINI_TARGETS,
}


def source_codes(backend: str) -> tuple[str, ...]:
    return _SOURCES[backend]


def target_codes(backend: str) -> tuple[str, ...]:
    return _TARGETS[backend]


def sources_for(backend: str) -> list[dict[str, str]]:
    return _ordered(source_codes(backend))


def targets_for(backend: str) -> list[dict[str, str]]:
    return _ordered(target_codes(backend))


def name_of(code: str) -> str:
    """English name for a code, falling back to the code itself.

    The fallback is deliberate: an unknown code should degrade to showing "xx"
    rather than raising inside a live call.
    """
    lang = ALL.get(code)
    return lang.label if lang else code


# Backwards-compatible view for code that just wants code -> English name.
SUPPORTED_LANGUAGES: dict[str, str] = {c: l.label for c, l in ALL.items()}
