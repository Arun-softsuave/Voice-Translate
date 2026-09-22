"""How a translation backend is selected and wired to a session.

Two things are covered here:

1. **Backend selection.** `OPENAI_TRANSLATE_MODE` chooses between
   `gpt-realtime-translate` and `gpt-realtime-2.1`. Getting this wrong means
   silently running the model you did not intend.

2. **The language pair**, which is regression cover for a real bug: in echo
   mode the listener and the speaker are the same participant, and using that
   participant for the LANGUAGE PAIR made the session translate a language into
   itself — so every utterance came back in the speaker's own language.

Echo mode must change only where translated audio is played, never what it is
translated into.
"""

from dataclasses import replace

import pytest

from app.config import get_settings
from app.models.session import ParticipantId
from app.services.realtime_service import RealtimeTranslator
from app.services.session_service import SessionRegistry
from app.services.translate_service import TranslateSession
from app.websocket import media_stream


class Recorder:
    """Captures the arguments a backend would be constructed with."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, settings, **kwargs):
        self.kwargs = kwargs
        return self

    @property
    def __name__(self):
        return "Recorder"

    async def connect(self):
        return None


@pytest.fixture
def session():
    registry = SessionRegistry()
    # A speaks Tamil, B hears Hindi.
    return registry.create(source_language="ta", target_language="hi")


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(media_stream, "translator_class", lambda settings: rec)
    return rec


def enabled(echo: bool = False, mode: str = "translate"):
    return replace(get_settings(), openai_api_key="sk-test",
                   echo_mode=echo, openai_translate_mode=mode)


# ------------------------------------------------------------ selection
def test_translate_mode_selects_the_translate_backend():
    assert media_stream.translator_class(enabled(mode="translate")) is TranslateSession


def test_realtime_mode_selects_the_conversational_backend():
    assert media_stream.translator_class(enabled(mode="realtime")) is RealtimeTranslator


def test_translate_is_the_default_mode():
    assert get_settings().openai_translate_mode == "translate"
    assert get_settings().use_translate_backend is True


def test_translation_model_reports_the_one_actually_in_use():
    assert enabled(mode="translate").translation_model == "gpt-realtime-translate"
    assert enabled(mode="realtime").translation_model == "gpt-realtime-2.1"


# -------------------------------------------------------- language pair
@pytest.mark.asyncio
async def test_language_pair_is_speaker_to_counterpart(session, recorder):
    await media_stream._open_translator(enabled(), session, ParticipantId.A)

    assert recorder.kwargs["source_language"] == "ta"
    assert recorder.kwargs["target_language"] == "hi"


@pytest.mark.asyncio
async def test_echo_mode_does_not_collapse_the_language_pair(session, recorder):
    """The bug: this used to produce ta -> ta."""
    await media_stream._open_translator(enabled(echo=True), session, ParticipantId.A)

    assert recorder.kwargs["source_language"] == "ta"
    assert recorder.kwargs["target_language"] == "hi"
    assert recorder.kwargs["source_language"] != recorder.kwargs["target_language"]


@pytest.mark.asyncio
async def test_reverse_direction_is_mirrored(session, recorder):
    await media_stream._open_translator(enabled(), session, ParticipantId.B)

    assert recorder.kwargs["source_language"] == "hi"
    assert recorder.kwargs["target_language"] == "ta"


@pytest.mark.asyncio
async def test_reverse_direction_in_echo_mode_is_also_correct(session, recorder):
    await media_stream._open_translator(enabled(echo=True), session, ParticipantId.B)

    assert recorder.kwargs["source_language"] == "hi"
    assert recorder.kwargs["target_language"] == "ta"


@pytest.mark.asyncio
async def test_iso_codes_are_passed_not_display_names(recorder):
    """The interface carries codes; each backend formats them as it needs."""
    registry = SessionRegistry()
    s = registry.create(source_language="bn", target_language="gu")

    await media_stream._open_translator(enabled(), s, ParticipantId.A)

    assert recorder.kwargs["source_language"] == "bn"
    assert recorder.kwargs["target_language"] == "gu"


@pytest.mark.asyncio
async def test_no_translator_without_an_api_key(session, recorder):
    settings = replace(get_settings(), openai_api_key="", echo_mode=True)

    result = await media_stream._open_translator(settings, session, ParticipantId.A)

    assert result is None, "audio must pass through when translation is unconfigured"
    assert recorder.kwargs is None
