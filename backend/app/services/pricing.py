"""OpenAI Realtime token accounting.

Prices per 1M tokens, from the official OpenAI pricing page, checked
21 Sep 2026. The `usage` object on `response.done` is stated to correspond to
billing, so these figures applied to that object give real cost, not estimates.

Note the cached rate: repeated context within a session bills at $0.40/1M
instead of $32/1M for audio — 80x cheaper. In a long call most of the
conversation history is cached, so a naive "audio minutes x $32/1M" estimate
badly overstates the bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PRICES: dict[str, dict[str, float]] = {
    "gpt-realtime-2.1": {
        "audio_in": 32.00, "audio_cached": 0.40, "audio_out": 64.00,
        "text_in": 4.00, "text_cached": 0.40, "text_out": 24.00,
    },
    "gpt-realtime-2.1-mini": {
        "audio_in": 10.00, "audio_cached": 0.30, "audio_out": 20.00,
        "text_in": 0.60, "text_cached": 0.06, "text_out": 2.40,
    },
}

# Same pricing per OpenAI's table.
PRICES["gpt-realtime-2"] = PRICES["gpt-realtime-2.1"]
PRICES["gpt-realtime-mini"] = PRICES["gpt-realtime-2.1-mini"]

USD_TO_INR = 95.96


def prices_for(model: str) -> dict[str, float]:
    return PRICES.get(model, PRICES["gpt-realtime-2.1"])


@dataclass
class Usage:
    """Running token totals for one Realtime session."""

    model: str = "gpt-realtime-2.1"
    responses: int = 0
    audio_in: int = 0
    audio_cached: int = 0
    text_in: int = 0
    text_cached: int = 0
    audio_out: int = 0
    text_out: int = 0

    def add(self, usage: dict) -> dict:
        """Fold one response.done usage object in. Returns that response's own
        counts, so a single utterance can be logged as well as the total."""
        ind = usage.get("input_token_details") or {}
        outd = usage.get("output_token_details") or {}

        cached_details = ind.get("cached_tokens_details") or {}
        cached_audio = int(cached_details.get("audio_tokens", 0) or 0)
        cached_text = int(cached_details.get("text_tokens", 0) or 0)
        # Older shapes report only a flat cached_tokens count.
        if not cached_audio and not cached_text:
            cached_audio = int(ind.get("cached_tokens", 0) or 0)

        audio_in = int(ind.get("audio_tokens", 0) or 0)
        text_in = int(ind.get("text_tokens", 0) or 0)

        # cached tokens are a SUBSET of input tokens, so bill them once
        fresh_audio_in = max(audio_in - cached_audio, 0)
        fresh_text_in = max(text_in - cached_text, 0)

        one = {
            "audio_in": fresh_audio_in,
            "audio_cached": cached_audio,
            "text_in": fresh_text_in,
            "text_cached": cached_text,
            "audio_out": int(outd.get("audio_tokens", 0) or 0),
            "text_out": int(outd.get("text_tokens", 0) or 0),
        }

        self.responses += 1
        for key, value in one.items():
            setattr(self, key, getattr(self, key) + value)

        one["usd"] = self.cost_of(one)
        return one

    def cost_of(self, counts: dict) -> float:
        p = prices_for(self.model)
        return round(
            sum(counts.get(k, 0) * p[k] for k in
                ("audio_in", "audio_cached", "text_in", "text_cached",
                 "audio_out", "text_out")) / 1_000_000,
            6,
        )

    @property
    def usd(self) -> float:
        return self.cost_of(self.as_counts())

    @property
    def inr(self) -> float:
        return round(self.usd * USD_TO_INR, 4)

    def as_counts(self) -> dict:
        return {
            "audio_in": self.audio_in, "audio_cached": self.audio_cached,
            "text_in": self.text_in, "text_cached": self.text_cached,
            "audio_out": self.audio_out, "text_out": self.text_out,
        }

    def summary(self) -> dict:
        counts = self.as_counts()
        return {
            "model": self.model,
            "responses": self.responses,
            **counts,
            "total_tokens": sum(counts.values()),
            "usd": self.usd,
            "inr": self.inr,
        }
