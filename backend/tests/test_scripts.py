"""Is a transcript written in the language the caller chose?

Gemini is told what to SPEAK but never told what it is HEARING: the source
language is detected, and `language_codes` is a hint it may overrule. On 8 kHz
phone audio it sometimes does, and then the whole call is transcribed in
another language while the translated audio stays perfectly correct - the
decision is made on the first utterance and everything follows it.

Two production calls, six minutes apart, same languages:

    call 79   hello ప్రదీప్ నా పేరు శ్రీకాంత్    -> Telugu, every line
    call 80   நான் பேசுறது கேக்குதா?            -> Tamil, every line

We know what the answer should be, because the caller picked it, so this is
arithmetic rather than inference. These tests use the real text from those
calls: anything here that stops failing on them has stopped working.
"""

import pytest

from app.services import scripts

# Verbatim from the production transcripts and the reported screenshot.
TAMIL = "இந்த அழைப்பு மொழிபெயர்க்கப்படுகிறது"
TAMIL_REAL = "ஆமாங்க கரெக்டா இது இப்ப இருக்கு"
TELUGU = "హలో ప్రదీప్ నా పేరు శ్రీకాంత్"
DEVANAGARI = "हॅलो, मला बोलता येतंय का? ब्रो?"
HINDI = "इस कॉल का अनुवाद हो रहा है"


def test_the_two_failures_we_actually_saw_are_caught():
    assert scripts.wrong_script("ta", TELUGU) == "telugu"
    assert scripts.wrong_script("ta", DEVANAGARI) == "devanagari"


@pytest.mark.parametrize("text", [TAMIL, TAMIL_REAL])
def test_real_tamil_raises_no_complaint(text):
    assert scripts.wrong_script("ta", text) is None


def test_a_language_is_not_confused_with_its_neighbours():
    """Tamil, Telugu, Kannada and Malayalam have separate Unicode blocks."""
    assert scripts.wrong_script("te", TELUGU) is None
    assert scripts.wrong_script("hi", HINDI) is None
    assert scripts.wrong_script("te", TAMIL) == "tamil"
    assert scripts.wrong_script("hi", TAMIL) == "tamil"


def test_borrowed_words_do_not_make_a_line_wrong():
    """Real speech is full of English: "ticket", "project", names."""
    assert scripts.wrong_script(
        "ta", "நான் அந்த ticket வேலை பார்க்குறேன்") is None
    assert scripts.wrong_script(
        "hi", "मैं project पर काम कर रहा हूँ") is None


def test_latin_languages_are_never_judged():
    """English, Spanish and French share an alphabet; silence is honesty."""
    for language in ("en", "es", "fr", "de", "pt"):
        assert scripts.script_for(language) is None
        assert scripts.wrong_script(language, "Hello, can you hear me?") is None


def test_too_little_text_is_not_judged():
    """A name or an "OK" says nothing about the language spoken."""
    for text in ("OK", "Hmm", "4821", "...", ""):
        assert scripts.wrong_script("ta", text) is None


def test_an_unmapped_language_is_not_judged():
    assert scripts.wrong_script("xx", TELUGU) is None


def test_japanese_may_be_either_of_its_scripts():
    assert scripts.wrong_script("ja", "この通話は通訳されています") is None
    assert scripts.wrong_script("ja", "はなしたあとすこしまってください") is None


def test_every_language_the_app_offers_is_decided_either_way():
    """Each is mapped to a script or deliberately left unjudged - never by
    accident, which a bare lookup would not tell apart."""
    from app.services import languages

    for code in languages.ALL:
        script = scripts.script_for(code)
        assert script is None or script in scripts.BLOCKS, \
            f"{code}: {script!r} is not a known writing system"
