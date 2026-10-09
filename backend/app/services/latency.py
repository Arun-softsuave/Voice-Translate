"""Where a translated call spends its time.

One `LatencyTracker` per speaker. It turns the events the media stream already
sees (speech start and stop, model transcripts, translated audio chunks, Twilio
mark echoes) into per-sentence timings, so a demo call's log says exactly which
stage was slow.

Why two lags and not one. Measuring "speaker starts -> translation ends" would
add the length of the sentence to the delay. The model translates while the
person is still talking, so instead:

    start_lag = translation starts playing - speaker starts
    end_lag   = translation finishes playing - speaker stops

Neither depends on how long someone speaks.

Stages of the start lag:

    model_ms   speaker starts -> first translated speech reaches our server
               (Gemini itself, plus the network to Google)
    twilio_ms  our server sends it -> Twilio confirms it was played
               (our server -> ngrok -> Twilio, its playback queue, the echo back)

Turn assignment. There is one translator per speaker, so output never mixes
speakers. Within a speaker, speech that restarts within TURN_GAP_S of the last
stop extends the same turn, and a translated chunk arriving at T belongs to the
latest turn that started at least MIN_LAG_S before T. The model never answers
within a second (measured minimum ~2.5 s), so consecutive turns are only
confused if the lag exceeds the merge gap.

Everything here is pure: no I/O, no logging, an injectable clock. It never sees
what was said, only when.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TURN_GAP_S = 3.0        # same-speaker speech this close together is one turn
MIN_LAG_S = 1.0         # output this soon after a turn starts belongs to the one before
MARK_EVERY_S = 0.5      # at most one playback mark per turn per half second
IDLE_FINAL_S = 5.0      # a turn with output is final this long after its last chunk
NO_OUTPUT_S = 8.0       # speech with no translation this long after it stopped
SPEECH_RMS = 500.0      # translated chunks quieter than this are not speech
MARK_PREFIX = "lat:"
MAX_INBOUND_SAMPLES = 200_000


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


def _pct(values: list[float], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0])
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


@dataclass
class Turn:
    id: int
    start: float
    stop: float | None = None
    heard: float | None = None
    out_first: float | None = None
    out_last: float | None = None
    first_chunk_s: float = 0.0      # length of the first speech chunk
    play_first: float | None = None
    # (ack time - send time) of the latest acknowledged mark: Twilio's delay
    # at that point, used to project when the very last chunk was played.
    play_delay: float | None = None
    marks: dict[str, float] = field(default_factory=dict)   # name -> sent at
    last_mark_at: float | None = None
    seq: int = 0
    done: bool = False

    @property
    def speaking(self) -> bool:
        return self.stop is None

    def record(self) -> dict:
        play_last = (self.out_last + self.play_delay
                     if self.out_last is not None and self.play_delay is not None
                     else None)
        diff = lambda a, b: _ms(a - b) if a is not None and b is not None else None
        return {
            "turn": self.id,
            "speech_ms": diff(self.stop, self.start),
            "heard_ms": diff(self.heard, self.start),
            "model_ms": diff(self.out_first, self.start),
            "twilio_ms": diff(self.play_first, self.out_first),
            "start_lag_ms": diff(self.play_first, self.start),
            "end_lag_ms": diff(play_last, self.stop),
            "output_ms": diff(self.out_last, self.out_first),
            "no_output": self.out_first is None,
        }


SUMMARY_FIELDS = ("speech_ms", "heard_ms", "model_ms", "twilio_ms",
                  "start_lag_ms", "end_lag_ms")


class LatencyTracker:
    """Timings for one speaker's sentences and their translations."""

    def __init__(self, speaker: str) -> None:
        self.speaker = speaker
        self._turns: list[Turn] = []
        self._next_id = 1
        self.records: list[dict] = []
        self.cleared = 0
        self.dropped_ms = 0.0
        self.orphan_chunks = 0      # speech output we could not tie to a turn

    # --- events -------------------------------------------------------------
    def speech_started(self, t: float) -> None:
        last = self._turns[-1] if self._turns else None
        if last is not None and not last.done:
            if last.speaking:
                return
            if t - last.stop <= TURN_GAP_S:
                last.stop = None            # a pause, not a new sentence
                return
        self._turns.append(Turn(id=self._next_id, start=t))
        self._next_id += 1

    def speech_stopped(self, t: float) -> None:
        last = self._turns[-1] if self._turns else None
        if last is not None and last.speaking:
            last.stop = max(t, last.start)

    def heard(self, t: float) -> None:
        turn = self._turn_for(t)
        if turn is not None and turn.heard is None:
            turn.heard = t

    def audio_out(self, t: float, is_speech: bool, duration_s: float = 0.0) -> str | None:
        """Record one translated chunk. Returns a mark name to send after it.

        `duration_s` is how long the chunk plays. Twilio echoes a mark once the
        audio before it has *finished*, so the first chunk's length is taken
        off to get when the translation *started* playing.
        """
        if not is_speech:
            return None
        turn = self._turn_for(t)
        if turn is None:
            self.orphan_chunks += 1
            return None
        if turn.out_first is None:
            turn.out_first = t
            turn.first_chunk_s = duration_s
        turn.out_last = t
        if turn.last_mark_at is not None and t - turn.last_mark_at < MARK_EVERY_S:
            return None
        turn.seq += 1
        turn.last_mark_at = t
        name = f"{MARK_PREFIX}{self.speaker}:{turn.id}:{turn.seq}"
        turn.marks[name] = t
        return name

    def played(self, name: str, t: float) -> None:
        """Twilio echoed one of our marks: the audio before it has played."""
        try:
            turn_id = int(name.split(":")[2])
        except (IndexError, ValueError):
            return
        turn = next((x for x in self._turns if x.id == turn_id), None)
        if turn is None or name not in turn.marks:
            return
        sent = turn.marks.pop(name)
        if name.endswith(":1"):
            # The echo comes when the first chunk has finished playing.
            turn.play_first = t - turn.first_chunk_s
        turn.play_delay = t - sent

    def playback_cleared(self, dropped_ms: float) -> None:
        """Barge-in cut this speaker's translation while it was playing."""
        self.cleared += 1
        self.dropped_ms += max(dropped_ms, 0.0)

    # --- finishing ------------------------------------------------------------
    def finalize_due(self, now: float) -> list[dict]:
        out = []
        for i, turn in enumerate(self._turns):
            if turn.done:
                continue
            later_has_output = any(x.out_first is not None for x in self._turns[i + 1:])
            idle = (turn.out_last is not None and not turn.speaking
                    and now - turn.out_last >= IDLE_FINAL_S)
            silent = (turn.out_first is None and not turn.speaking
                      and now - turn.stop >= NO_OUTPUT_S)
            if later_has_output or idle or silent:
                out.append(self._finish(turn))
        self._forget_done()
        return out

    def flush(self, now: float) -> list[dict]:
        """End of call: finish everything still open."""
        out = []
        for turn in self._turns:
            if turn.done:
                continue
            if turn.speaking:
                turn.stop = now
            out.append(self._finish(turn))
        self._forget_done()
        return out

    def summary(self) -> dict:
        done = [r for r in self.records if not r["no_output"]]
        result = {
            "turns": len(self.records),
            "no_output": sum(1 for r in self.records if r["no_output"]),
            "cleared": self.cleared,
            "dropped_ms": round(self.dropped_ms),
            "orphan_chunks": self.orphan_chunks,
        }
        for name in SUMMARY_FIELDS:
            values = [r[name] for r in done if r[name] is not None]
            result[f"{name}_p50"] = _pct(values, 0.5)
            result[f"{name}_p90"] = _pct(values, 0.9)
            result[f"{name}_max"] = max(values) if values else None
        return result

    # --- internals -------------------------------------------------------------
    def _turn_for(self, t: float) -> Turn | None:
        for turn in reversed(self._turns):
            if turn.start <= t - MIN_LAG_S:
                return None if turn.done else turn
        return None

    def _finish(self, turn: Turn) -> dict:
        turn.done = True
        record = turn.record()
        self.records.append(record)
        return record

    def _forget_done(self) -> None:
        # Keep the newest finished turn: late audio for it must still map to it
        # rather than to an older turn.
        while len(self._turns) > 1 and self._turns[0].done and self._turns[1].done:
            self._turns.pop(0)


class MarkClock:
    """Round trip of the periodic `chk-` marks on one leg.

    Twilio echoes a mark once all audio queued before it has played. With
    nothing queued that is a pure server <-> Twilio round trip, so the minimum
    over a call is the network round trip; half of it is the inbound leg we
    cannot otherwise see.
    """

    def __init__(self) -> None:
        self._sent: dict[str, float] = {}
        self.rtts: list[float] = []

    def sent(self, name: str, t: float) -> None:
        self._sent[name] = t
        if len(self._sent) > 1000:                  # unanswered, e.g. leg gone
            self._sent.pop(next(iter(self._sent)))

    def acked(self, name: str, t: float) -> float | None:
        sent = self._sent.pop(name, None)
        if sent is None:
            return None
        rtt = t - sent
        self.rtts.append(rtt)
        return rtt

    @property
    def rtt_ms(self) -> int | None:
        return _ms(min(self.rtts)) if self.rtts else None


class InboundClock:
    """Is audio piling up before it reaches our code?

    Twilio stamps every frame with milliseconds since the stream started. The
    gap between that and when we read the frame is constant plus network jitter
    unless something stalls; growth means a backlog (a blocked event loop, a
    slow tunnel), and its size is exactly how late the speech got to us.
    """

    def __init__(self) -> None:
        self._offsets: list[float] = []

    def frame(self, twilio_ts_ms, t: float) -> None:
        try:
            ts = float(twilio_ts_ms)
        except (TypeError, ValueError):
            return
        if len(self._offsets) < MAX_INBOUND_SAMPLES:
            self._offsets.append(t * 1000.0 - ts)

    def summary(self) -> dict:
        if not self._offsets:
            return {"inbound_backlog_max_ms": None, "inbound_backlog_p90_ms": None}
        base = min(self._offsets)
        lags = [o - base for o in self._offsets]
        return {"inbound_backlog_max_ms": round(max(lags)),
                "inbound_backlog_p90_ms": _pct(lags, 0.9)}
