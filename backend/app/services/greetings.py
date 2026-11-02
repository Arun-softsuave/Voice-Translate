"""Short clips of real speech, one per language, used to prime a translator.

Gemini decides what language it is listening to from the first thing it hears
and keeps that decision for the whole session. A production call that opened
with "Hello Pradeep, my name is Srikanth" - names and English loanwords, with
no Tamil grammar to grip - was transcribed in Telugu from its first line to
its last, while the translated audio stayed perfectly correct throughout. The
next call opened with plainly Tamil speech and was right all the way.

So rather than leave that decision to whatever the caller happens to say
first, we hand the model a sentence of the right language before the call
starts. These are the clips `scripts/make_greetings.py` generates - genuine
synthesised speech, already in the repo for an earlier design that spoke them
down the line.

A language with no clip is simply not primed. That is the safe direction, and
why a missing file needs no handling anywhere else.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.utils import audio

log = logging.getLogger("app.greetings")

CLIP_DIR = Path(__file__).resolve().parent.parent / "data" / "greetings"

FRAME_MS = 20
FRAME_BYTES = 8 * FRAME_MS          # µ-law is one byte per sample at 8 kHz

_cache: dict[str, list[str]] = {}


def frames(language: str) -> list[str]:
    """The clip for one language as base64 media frames, read once and kept."""
    if language in _cache:
        return _cache[language]

    path = CLIP_DIR / f"{language}.ulaw"
    try:
        raw = path.read_bytes()
    except OSError:
        _cache[language] = []
        return _cache[language]

    whole = len(raw) // FRAME_BYTES          # a part-frame would click
    _cache[language] = [
        audio.b64_encode(raw[i * FRAME_BYTES:(i + 1) * FRAME_BYTES])
        for i in range(whole)
    ]
    log.info("prime_clip_loaded",
             extra={"language": language,
                    "seconds": round(whole * FRAME_MS / 1000, 1)})
    return _cache[language]


def available(language: str) -> bool:
    return bool(frames(language))


def languages() -> list[str]:
    """Every language we can prime, from what is on disk."""
    return sorted(p.stem for p in CLIP_DIR.glob("*.ulaw"))
