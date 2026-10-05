"""Live captions for one call.

Each translator reports two text streams for the participant it hears:

* **heard**: what that participant said, in their own language
  (the model's input transcript)
* **spoke**: the translation it produced, in the listener's language
  (the model's output transcript)

The model sends both as small fragments ("Can", " you", " come"). This module
joins them into lines and fans them out to whoever is watching the call.

What a viewer sees follows the audio routing rule, so every caption on a
screen is in that viewer's language:

* their own **heard** line, which is what they said
* the **spoke** line of whichever translator's audio is played to them: the
  peer's normally, their own in echo mode, exactly as `route_audio` decides

Captions live in memory for the life of the call and are dropped with the
session. Their text is never written to a log.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from app.models.session import ParticipantId, TranslationSession

# A pause longer than this in one stream starts a new line. Gap-based rather
# than tied to voice activity: translated text trails the speech by a few
# seconds, so a "speech started" edge would cut the previous translation in
# half.
GAP_S = 1.2
MAX_LINES = 200
# A watcher this far behind is dropped rather than buffered without limit.
MAX_QUEUED = 1000

HEARD = "heard"
SPOKE = "spoke"


@dataclass
class Line:
    id: int
    owner: "ParticipantId"     # whose translator produced it
    kind: str                  # HEARD | SPOKE
    text: str
    t: float                   # seconds since the call started
    updated: float             # monotonic time of the last fragment


class CaptionLog:
    """Lines for one session, plus the queues of everyone watching them."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._lines: deque[Line] = deque(maxlen=MAX_LINES)
        self._current: dict[tuple, Line] = {}
        self._next_id = 1
        self._watchers: set[asyncio.Queue] = set()
        self.closed = False

    # --- writing ----------------------------------------------------------
    def add(self, owner: "ParticipantId", kind: str, fragment: str) -> None:
        if self.closed or not fragment:
            return
        now = self._clock()
        key = (owner, kind)
        line = self._current.get(key)

        if line is None or now - line.updated > GAP_S:
            text = fragment.lstrip()
            if not text:
                return
            line = Line(id=self._next_id, owner=owner, kind=kind, text=text,
                        t=round(now - self._started, 1), updated=now)
            self._next_id += 1
            self._lines.append(line)
            self._current[key] = line
        else:
            line.text += fragment
            line.updated = now

        # Copies, so what a watcher reads is the line as it was at this moment.
        update = replace(line)
        for queue in list(self._watchers):
            try:
                queue.put_nowait(update)
            except asyncio.QueueFull:
                self._watchers.discard(queue)

    # --- reading ----------------------------------------------------------
    def lines(self) -> list[Line]:
        return [replace(line) for line in self._lines]

    def subscribe(self) -> tuple[list[Line], asyncio.Queue]:
        """Everything so far, plus a queue of updates from now on.

        A `None` on the queue means the call is over.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUED)
        if self.closed:
            queue.put_nowait(None)
        else:
            self._watchers.add(queue)
        return self.lines(), queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._watchers.discard(queue)

    @property
    def watchers(self) -> int:
        return len(self._watchers)

    def close(self) -> None:
        """End every open stream and forget what was said."""
        if self.closed:
            return
        self.closed = True
        for queue in self._watchers:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._watchers.clear()
        self._lines.clear()
        self._current.clear()


def view(line: Line, viewer: "ParticipantId", session: "TranslationSession",
         *, echo_mode: bool = False) -> dict | None:
    """How `line` appears to `viewer`, or None if it is not theirs to see."""
    owner = session.participant(line.owner)
    listener = session.participant(line.owner if echo_mode else line.owner.peer)

    if line.kind == HEARD:
        if line.owner is not viewer:
            return None
        speaker, lang, source = "you", owner.language, None
    else:
        if listener.participant_id is not viewer:
            return None
        # What you hear. In echo mode that is your own words, translated.
        speaker = "them"
        # A translator speaks the counterpart's language; see _open_translator.
        lang = session.peer_of(line.owner).language
        source = owner.language

    return {
        "id": line.id,
        "speaker": speaker,
        "kind": line.kind,
        "text": line.text.strip(),
        "lang": lang,
        "from": source,
        "t": line.t,
    }
