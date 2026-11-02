"""Telling a session which language it is listening to, before anyone speaks.

Gemini decides what language it is hearing from the first thing it hears, and
keeps that decision for the whole session. A production call that opened with
"Hello Pradeep, my name is Srikanth" - names and English loanwords, with no
Tamil grammar to grip - was transcribed in Telugu from its first line to its
last, while the translated audio stayed correct throughout. The next call
opened with plainly Tamil speech and was right all the way.

So the first thing it hears is now ours to choose: a clip of real speech in
the speaker's own language. Everything the model makes of that clip is thrown
away, and the throwing-away ends on the model's own `turn_complete` rather
than after a guessed number of seconds. A guess would either leak a stray
greeting into the call or swallow the first thing somebody said.
"""

import asyncio
from dataclasses import replace

import pytest
from google.genai import live as sdk_live

from app.config import get_settings
from app.models.session import ParticipantId
from app.services import greetings
from app.services.session_service import registry
from app.websocket import media_stream
from tests.test_gemini_service import FakeWire

A, B = ParticipantId.A, ParticipantId.B


@pytest.fixture
def wires(monkeypatch):
    made = []

    def fake_ws_connect(uri, additional_headers=None, **kwargs):
        wire = FakeWire(uri, additional_headers)
        made.append(wire)
        return wire

    monkeypatch.setattr(sdk_live, "ws_connect", fake_ws_connect)
    return made


@pytest.fixture
def gemini():
    return replace(get_settings(), translation_backend="gemini",
                   gemini_api_key="g-test")


@pytest.fixture
def session():
    s = registry.create(source_language="ta", target_language="en")
    yield s
    registry.remove(s.session_id)


# --- the clips themselves --------------------------------------------------

def test_there_is_a_clip_for_the_languages_this_is_for():
    """Tamil above all: it is the language the fault was reported against."""
    for language in ("ta", "en", "hi"):
        assert greetings.available(language), f"no priming clip for {language}"


def test_frames_are_the_size_twilio_and_the_model_expect():
    from app.utils import audio
    for frame in greetings.frames("ta")[:20]:
        assert len(audio.b64_decode(frame)) == greetings.FRAME_BYTES


def test_a_language_with_no_clip_is_simply_not_primed():
    """The safe direction: no clip means no priming, never a failure."""
    assert greetings.frames("zz") == []
    assert greetings.available("zz") is False


# --- priming a real session ------------------------------------------------

@pytest.mark.asyncio
async def test_the_session_is_primed_with_the_speakers_language(
        wires, gemini, session):
    translator = await media_stream._open_translator(gemini, session, A)
    try:
        assert translator.priming is True, "the session was never primed"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_what_the_model_says_back_to_the_clip_is_discarded(
        wires, gemini, session):
    """Otherwise the far side hears a stray greeting before the call begins."""
    translator = await media_stream._open_translator(gemini, session, A)
    try:
        wires[0].push({"serverContent": {
            "inputTranscription": {"text": "இந்த அழைப்பு"}}})
        wires[0].push({"serverContent": {
            "outputTranscription": {"text": "This call"}}})
        await asyncio.sleep(0.05)

        assert session.captions.lines() == [], \
            "the priming clip was captioned as if somebody had said it"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_the_models_own_turn_ends_the_discarding(wires, gemini, session):
    """Not a timer: a guess would leak a greeting or swallow a real word."""
    translator = await media_stream._open_translator(gemini, session, A)
    try:
        wires[0].push({"serverContent": {"turnComplete": True}})
        await asyncio.sleep(0.05)
        assert translator.priming is False

        wires[0].push({"serverContent": {
            "inputTranscription": {"text": "நான் பேசுறது கேக்குதா?"}}})
        await asyncio.sleep(0.05)

        assert [l.text for l in session.captions.lines()] == \
            ["நான் பேசுறது கேக்குதா?"], "real speech was discarded as priming"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_the_backstop_ends_the_discarding_without_letting_that_one_through(
        wires, gemini, session, monkeypatch):
    """This model never sends `turn_complete`, so the deadline is what stops
    the discarding - and it must discard the message that trips it.

    Letting one through was a real fault: the clip's translation reached the
    phone, played a fragment of the greeting down the line, and cut the
    waiting music off after 2.4 seconds. Only that one message is lost, and
    only on a leg primed while the phone is still ringing.
    """
    translator = await media_stream._open_translator(gemini, session, A)
    try:
        assert translator.priming is True
        monkeypatch.setattr(translator, "_prime_deadline", 0.0)   # expired

        wires[0].push({"serverContent": {
            "outputTranscription": {"text": "This call is being translated"}}})
        await asyncio.sleep(0.05)
        assert translator.priming is False
        assert session.captions.lines() == [],             "the priming clip escaped into the call"

        # Everything after it flows normally.
        wires[0].push({"serverContent": {
            "inputTranscription": {"text": "நான் பேசுறது கேக்குதா?"}}})
        await asyncio.sleep(0.05)
        assert [l.text for l in session.captions.lines()] ==             ["நான் பேசுறது கேக்குதா?"], "the call stayed mute after priming"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_only_the_caller_leg_is_primed(wires, gemini, session):
    """The phone's translator opens about a second before its stream binds -
    no quiet window - and priming it once leaked a greeting into a live call.
    The caller's opens while the phone is still ringing, where nothing it
    produces can reach anybody."""
    caller = await media_stream._open_translator(gemini, session, A)
    phone = await media_stream._open_translator(gemini, session, B)
    try:
        assert caller.priming is True, "the caller leg was not primed"
        assert phone.priming is False, "the phone leg must not be primed"
    finally:
        await caller.close()
        await phone.close()


# --- what actually ends the discarding -------------------------------------

@pytest.mark.asyncio
async def test_the_persons_first_real_frame_ends_the_discarding(
        wires, gemini, session):
    """The one signal that always arrives.

    A live call left priming running for 15.3 seconds against a 6-second cap,
    because the cap is only noticed when the model sends something and this
    model sent nothing. The caller's whole opening was discarded - leg A
    finished with heard_lines: 0 - and the clip's own translation reached the
    phone as "This call", cutting the waiting music off after 2.6 seconds.
    """
    from app.utils import audio

    translator = await media_stream._open_translator(gemini, session, A)
    try:
        assert translator.priming is True

        await translator.append_audio(audio.b64_encode(b"\xff" * 160))
        assert translator.priming is False, \
            "the caller's own audio did not end priming"

        wires[0].push({"serverContent": {
            "inputTranscription": {"text": "நான் பேசுறது கேக்குதா?"}}})
        await asyncio.sleep(0.05)
        assert [l.text for l in session.captions.lines()] == \
            ["நான் பேசுறது கேக்குதா?"], "their speech was still being discarded"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_feeding_the_clip_does_not_end_the_discarding(
        wires, gemini, session):
    """Otherwise the clip would end its own window on its first frame."""
    translator = await media_stream._open_translator(gemini, session, A)
    try:
        from app.utils import audio
        for _ in range(5):
            await translator.prime_audio(audio.b64_encode(b"\xff" * 160))
        assert translator.priming is True, "the clip ended its own window"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_a_silent_model_cannot_keep_discarding_for_ever(
        wires, gemini, session):
    """The 15-second failure: nothing arrives, so nothing checks the cap.

    Ending on the caller's audio makes the model's silence irrelevant.
    """
    from app.utils import audio

    translator = await media_stream._open_translator(gemini, session, A)
    try:
        assert translator.priming is True
        # The model says nothing at all - no turn_complete, no transcript.
        await asyncio.sleep(0.05)
        assert translator.priming is True

        await translator.append_audio(audio.b64_encode(b"\xff" * 160))
        assert translator.priming is False, \
            "a silent model kept the caller muted"
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_the_clip_is_followed_by_silence(wires, gemini, session,
                                               monkeypatch):
    """So the model sees the turn END and answers inside the window.

    Without trailing silence the clip sits in the model's buffer, joins onto
    the start of the caller's real speech, and is answered seconds later. On
    a live call that put the clip's own translation - "This call" - into the
    phone 9.5 s after it was sent, cutting the waiting music short.
    """
    from app.utils import audio
    from app.websocket import media_stream as ms

    fed: list[str] = []

    translator = await ms._open_translator(gemini, session, A)
    try:
        await translator.close()
    except Exception:                                       # noqa: BLE001
        pass

    session.a.music_task = None
    translator2 = await ms._open_translator(gemini, session, A)
    monkeypatch.setattr(translator2, "prime_audio",
                        lambda frame: fed.append(frame) or asyncio.sleep(0))
    try:
        await ms._prime_language(session, A, session.a, translator2)
    finally:
        await translator2.close()

    assert len(fed) > ms.PRIME_FRAMES, "no silence was sent after the clip"
    silence = audio.b64_encode(audio.ulaw_silence(20))
    assert fed[-1] == silence, "the priming audio does not end in silence"
    assert fed.count(silence) >= ms.PRIME_HUSH_FRAMES
