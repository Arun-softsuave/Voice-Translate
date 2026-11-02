"""Is this transcript written in the language the caller chose?

Gemini is told what language to SPEAK - `translation_config.target_language_code`
is a setting, and the audio it produces is reliably correct. It is never told
what language it is HEARING: the source is detected, and `language_codes` is
only a hint it may overrule. On 8 kHz phone audio it sometimes does, and then
a Tamil speaker's words appear on screen in Telugu or Devanagari for the whole
call - the decision is made on the first utterance and everything follows it.

The translation stays correct throughout, because translating needs no name
for the input language. Only the written transcript has to commit to one.

We know what the answer should be, because the caller picked it. So this is
arithmetic, not inference: each of these languages has its own Unicode block,
and text in the wrong one is wrong with certainty.

Two things it cannot do, by design:

* Latin-script languages are indistinguishable here - English, Spanish and
  French share an alphabet. `script_for` returns None for those and every
  check passes, rather than pretending to a confidence it does not have.
* It judges script, not grammar. Telugu words written in Tamil script would
  pass. That has not been observed; wrong-script output has.
"""

from __future__ import annotations

import unicodedata

# Where each writing system lives, as (first, last) code points.
BLOCKS: dict[str, tuple[tuple[int, int], ...]] = {
    "tamil": ((0x0B80, 0x0BFF),),
    "telugu": ((0x0C00, 0x0C7F),),
    "kannada": ((0x0C80, 0x0CFF),),
    "malayalam": ((0x0D00, 0x0D7F),),
    "devanagari": ((0x0900, 0x097F),),
    "bengali": ((0x0980, 0x09FF),),
    "gujarati": ((0x0A80, 0x0AFF),),
    "gurmukhi": ((0x0A00, 0x0A7F),),
    "sinhala": ((0x0D80, 0x0DFF),),
    "arabic": ((0x0600, 0x06FF), (0x0750, 0x077F)),
    "hebrew": ((0x0590, 0x05FF),),
    "greek": ((0x0370, 0x03FF),),
    "cyrillic": ((0x0400, 0x04FF),),
    "armenian": ((0x0530, 0x058F),),
    "georgian": ((0x10A0, 0x10FF),),
    "ethiopic": ((0x1200, 0x137F),),
    "thai": ((0x0E00, 0x0E7F),),
    "lao": ((0x0E80, 0x0EFF),),
    "khmer": ((0x1780, 0x17FF),),
    "myanmar": ((0x1000, 0x109F),),
    "hangul": ((0xAC00, 0xD7AF), (0x1100, 0x11FF)),
    "kana": ((0x3040, 0x309F), (0x30A0, 0x30FF)),
    "han": ((0x4E00, 0x9FFF), (0x3400, 0x4DBF)),
}

# The language codes this app offers, and the script each is written in.
# Absent means "Latin, or not safely distinguishable" - see the docstring.
SCRIPTS: dict[str, str] = {
    "ta": "tamil", "te": "telugu", "kn": "kannada", "ml": "malayalam",
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
    "bn": "bengali", "gu": "gujarati", "pa": "gurmukhi", "si": "sinhala",
    "ur": "arabic", "fa": "arabic", "sd": "arabic", "ar": "arabic",
    "he": "hebrew", "el": "greek", "hy": "armenian", "ka": "georgian",
    "am": "ethiopic", "th": "thai", "lo": "lao", "km": "khmer",
    "my": "myanmar", "ko": "hangul", "zh": "han",
    "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic", "sr": "cyrillic",
    "mk": "cyrillic", "be": "cyrillic", "kk": "cyrillic", "mn": "cyrillic",
}

# Japanese mixes kana with Han, so either is right and neither alone is wrong.
ALSO_FINE: dict[str, tuple[str, ...]] = {
    "ja": ("kana", "han"),
    "zh": ("han",),
    "ko": ("hangul", "han"),
}

# Below this many letters there is not enough to judge: "OK" or a name tells
# us nothing about what language was spoken.
MIN_LETTERS = 4


def script_for(language: str) -> str | None:
    """The writing system this language uses, or None if we cannot tell."""
    return SCRIPTS.get(language)


def _script_of(char: str) -> str | None:
    point = ord(char)
    for name, ranges in BLOCKS.items():
        for first, last in ranges:
            if first <= point <= last:
                return name
    return None


def dominant_script(text: str) -> str | None:
    """The writing system most of this text is in, ignoring Latin and digits.

    Latin is skipped rather than counted: real speech is full of borrowed
    words and names ("ticket", "Pradeep"), and counting them would call every
    Indian-language line a mismatch.
    """
    counts: dict[str, int] = {}
    letters = 0
    for char in text:
        if not unicodedata.category(char).startswith("L"):
            continue
        letters += 1
        found = _script_of(char)
        if found is not None:
            counts[found] = counts.get(found, 0) + 1

    if letters < MIN_LETTERS or not counts:
        return None
    return max(counts, key=counts.get)


def wrong_script(language: str, text: str) -> str | None:
    """The script this text is actually in, when that is not the right one.

    None means "no complaint": either it matches, or there is no way to tell -
    too short, Latin-only, or a language we do not map. Silence here always
    means uncertainty, never a pass.
    """
    expected = script_for(language)
    if expected is None:
        return None

    found = dominant_script(text)
    if found is None or found == expected:
        return None
    if found in ALSO_FINE.get(language, ()):
        return None
    return found
