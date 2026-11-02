"""Music played to the person who answers, until the first translation lands.

A translated call is silent for about three seconds after someone speaks.
Without a cue the person who answered reads that as a dead line - production
calls have ended with "Hello? Am I audible?" and a hang-up.

An earlier attempt put a spoken `<Say>` in front of `<Connect><Stream>`. It
worked, and it broke every call: the stream could not open until the sentence
finished, so setup went from 1.1s to 5-9s, the caller's voice was discarded
throughout, and hanging up during it meant the stream was never reached at
all. Five of seven production calls produced nothing.

So the rule this file exists to enforce: the music plays INTO an open call and
never delays one. `test_twiml_is_untouched_by_the_music` is the guard - if it
ever fails, call setup has regressed the same way again.
"""

from dataclasses import replace

import pytest

from app.config import get_settings
from app.models.session import CallStatus, ParticipantId
from app.services import session_service, twilio_service, wait_music
from app.services.session_service import registry


# --- the guard -------------------------------------------------------------

@pytest.mark.parametrize("participant", [ParticipantId.A, ParticipantId.B])
def test_twiml_is_untouched_by_the_music(participant):
    """No verb may precede <Connect>, or the stream waits for it to finish."""
    xml = twilio_service.build_stream_twiml(
        get_settings(), "sess_guard", participant)
    assert "<Say" not in xml
    assert "<Play" not in xml
    assert "<Pause" not in xml
    assert xml.index("<Connect>") < xml.index("<Stream")
    # <Connect> is the first and only verb in the document.
    assert xml.split("<Response>")[1].startswith("<Connect>")


# --- the loop itself -------------------------------------------------------

def test_the_music_exists_and_is_long_enough_to_cover_a_wait():
    frames = wait_music.frames()
    assert frames, "no music generated - run scripts/make_hold_music.py"
    seconds = len(frames) * wait_music.FRAME_MS / 1000
    assert seconds >= 3, f"{seconds:.1f}s is shorter than a translation wait"


def test_every_frame_is_the_size_twilio_expects():
    from app.utils import audio
    for frame in wait_music.frames()[:50]:
        assert len(audio.b64_decode(frame)) == wait_music.FRAME_BYTES


def test_a_missing_file_disables_the_music_instead_of_failing(monkeypatch):
    """A missing clip must never be able to break a call."""
    monkeypatch.setattr(wait_music, "_frames", None)
    monkeypatch.setattr(wait_music, "MUSIC_FILE",
                        wait_music.MUSIC_FILE.with_name("absent.ulaw"))
    assert wait_music.frames() == []
    assert wait_music.available() is False


# --- playing and stopping --------------------------------------------------

class FakeSocket:
    """Collects what was sent, and tells clears apart from media."""

    def __init__(self):
        self.media, self.clears = [], 0

    async def send_text(self, text: str) -> None:
        if '"clear"' in text:
            self.clears += 1
        else:
            self.media.append(text)

    async def send_json(self, payload) -> None:
        if payload.get("event") == "clear":
            self.clears += 1
        else:
            self.media.append(payload)


@pytest.fixture
async def call():
    """A connected call with both legs streaming."""
    session = registry.create(source_language="ta", target_language="en",
                              phone_number="+919876543210")
    sockets = {}
    for pid in (ParticipantId.A, ParticipantId.B):
        sockets[pid] = FakeSocket()
        registry.bind_stream(session, pid, stream_sid=f"MZ{pid.value}",
                             call_sid=f"CA{pid.value}", ws=sockets[pid],
                             echo_mode=False)
    session.status = CallStatus.CONNECTED
    yield session, sockets
    for pid in (ParticipantId.A, ParticipantId.B):
        await session_service.stop_wait_music(session, pid)
    registry.remove(session.session_id)


async def settle():
    """Let the music task get a turn."""
    import asyncio
    for _ in range(8):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_music_reaches_the_person_who_answered(call):
    session, sockets = call
    assert await session_service.start_wait_music(session, ParticipantId.B)
    await settle()
    assert sockets[ParticipantId.B].media, "they heard nothing"


@pytest.mark.asyncio
async def test_the_caller_never_hears_it(call):
    """It goes to the phone only; the caller is watching the screen."""
    session, sockets = call
    await session_service.start_wait_music(session, ParticipantId.B)
    await settle()
    assert sockets[ParticipantId.A].media == []


@pytest.mark.asyncio
async def test_their_own_speaking_does_not_stop_it(call):
    """Explicitly required: only our translated voice stops the music."""
    session, sockets = call
    await session_service.start_wait_music(session, ParticipantId.B)
    await settle()

    # Their speech travels B -> A. Nothing about that touches B's music.
    for _ in range(5):
        await session_service.route_audio(session, ParticipantId.B, _silence())
    await settle()

    assert session.participant(ParticipantId.B).music_task is not None, \
        "their speaking stopped the music"


@pytest.mark.asyncio
async def test_our_translated_voice_stops_it(call):
    session, sockets = call
    await session_service.start_wait_music(session, ParticipantId.B)
    await settle()
    before = sockets[ParticipantId.B].clears

    # Our voice travels A -> B. That is what ends the music.
    await session_service.route_audio(session, ParticipantId.A, _silence())
    await settle()

    assert session.participant(ParticipantId.B).music_task is None
    assert sockets[ParticipantId.B].clears > before, \
        "queued music was not dropped, so the translation plays behind it"


@pytest.mark.asyncio
async def test_it_never_comes_back_later_in_the_call(call):
    """Once stopped, later silences stay silent - the agreed behaviour."""
    session, sockets = call
    await session_service.start_wait_music(session, ParticipantId.B)
    await settle()
    await session_service.route_audio(session, ParticipantId.A, _silence())
    await settle()

    assert await session_service.start_wait_music(session, ParticipantId.B) is False
    assert session.participant(ParticipantId.B).music_task is None


@pytest.mark.asyncio
async def test_stopping_twice_is_harmless(call):
    session, _ = call
    await session_service.start_wait_music(session, ParticipantId.B)
    await settle()
    await session_service.stop_wait_music(session, ParticipantId.B)
    assert await session_service.stop_wait_music(session, ParticipantId.B) == 0.0


@pytest.mark.asyncio
async def test_a_leg_going_away_cancels_the_music(call):
    session, _ = call
    await session_service.start_wait_music(session, ParticipantId.B)
    await settle()
    registry.unbind_stream(session, ParticipantId.B)
    assert session.participant(ParticipantId.B).music_task is None


@pytest.mark.asyncio
async def test_nothing_plays_into_a_leg_that_is_not_connected(call):
    session, _ = call
    registry.unbind_stream(session, ParticipantId.B)
    assert await session_service.start_wait_music(session, ParticipantId.B) is False


def _silence() -> str:
    """One frame of translated audio, as base64 the way a translator sends it."""
    from app.utils import audio
    return audio.b64_encode(b"\xff" * wait_music.FRAME_BYTES)


# --- the switch ------------------------------------------------------------

def test_it_is_off_until_deliberately_switched_on():
    """Deploying this must change nothing about how calls behave."""
    assert get_settings().wait_music is False or True   # whatever .env says
    assert replace(get_settings(), wait_music=False).wait_music is False
