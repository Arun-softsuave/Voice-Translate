"""How the translator is wired to a session.

Regression cover for a real bug: in echo mode the listener and the speaker are
the same participant, and using that participant for the LANGUAGE PAIR made the
session translate a language into itself — so every utterance came back in the
speaker's own language regardless of what was spoken.

Echo mode must change only the destination of the audio, never the language
pair.
"""

import pytest

from app.config import get_settings
from app.models.session import ParticipantId
from app.services.session_service import SessionRegistry
from app.websocket import media_stream


class Recorder:
    """Captures the arguments the translator would be constructed with."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, settings, **kwargs):
        self.kwargs = kwargs
        return self

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
    monkeypatch.setattr(media_stream, "RealtimeTranslator", rec)
    return rec


def enabled(echo: bool):
    from dataclasses import replace

    return replace(get_settings(), openai_api_key="sk-test", echo_mode=echo)


@pytest.mark.asyncio
async def test_language_pair_is_speaker_to_counterpart(session, recorder):
    await media_stream._open_translator(enabled(echo=False), session, ParticipantId.A)

    assert recorder.kwargs["source_language"] == "Tamil"
    assert recorder.kwargs["target_language"] == "Hindi"


@pytest.mark.asyncio
async def test_echo_mode_does_not_collapse_the_language_pair(session, recorder):
    """The bug: this used to produce Tamil -> Tamil."""
    await media_stream._open_translator(enabled(echo=True), session, ParticipantId.A)

    assert recorder.kwargs["source_language"] == "Tamil"
    assert recorder.kwargs["target_language"] == "Hindi"
    assert recorder.kwargs["source_language"] != recorder.kwargs["target_language"]


@pytest.mark.asyncio
async def test_reverse_direction_is_mirrored(session, recorder):
    await media_stream._open_translator(enabled(echo=False), session, ParticipantId.B)

    assert recorder.kwargs["source_language"] == "Hindi"
    assert recorder.kwargs["target_language"] == "Tamil"


@pytest.mark.asyncio
async def test_reverse_direction_in_echo_mode_is_also_correct(session, recorder):
    await media_stream._open_translator(enabled(echo=True), session, ParticipantId.B)

    assert recorder.kwargs["source_language"] == "Hindi"
    assert recorder.kwargs["target_language"] == "Tamil"


@pytest.mark.asyncio
async def test_no_translator_without_an_api_key(session, recorder):
    from dataclasses import replace

    settings = replace(get_settings(), openai_api_key="", echo_mode=True)

    result = await media_stream._open_translator(settings, session, ParticipantId.A)

    assert result is None, "audio must pass through when translation is unconfigured"
    assert recorder.kwargs is None


@pytest.mark.asyncio
async def test_language_codes_are_resolved_to_names_for_the_prompt(recorder):
    """The model is prompted with 'Tamil', not 'ta'."""
    registry = SessionRegistry()
    s = registry.create(source_language="bn", target_language="gu")

    await media_stream._open_translator(enabled(echo=False), s, ParticipantId.A)

    assert recorder.kwargs["source_language"] == "Bengali"
    assert recorder.kwargs["target_language"] == "Gujarati"
