"""Token accounting against the official OpenAI Realtime prices.

The subtlety worth testing: cached tokens are a SUBSET of input tokens, not an
extra line. Billing them on top of input would overstate every call, and
ignoring the cached rate entirely would overstate it by up to 80x on the
cached portion.
"""

import pytest

from app.services.pricing import PRICES, USD_TO_INR, Usage, prices_for


def test_official_rates_for_the_default_model():
    p = prices_for("gpt-realtime-2.1")
    assert p["audio_in"] == 32.00
    assert p["audio_cached"] == 0.40
    assert p["audio_out"] == 64.00
    assert p["text_in"] == 4.00
    assert p["text_out"] == 24.00


def test_mini_is_cheaper_on_every_axis():
    full, mini = prices_for("gpt-realtime-2.1"), prices_for("gpt-realtime-2.1-mini")
    for key in full:
        assert mini[key] < full[key], key


def test_unknown_model_falls_back_rather_than_crashing():
    assert prices_for("gpt-realtime-99") == PRICES["gpt-realtime-2.1"]


def test_cached_tokens_are_not_double_billed():
    """600 audio in, 400 of them cached -> bill 200 fresh + 400 cached."""
    u = Usage()
    one = u.add({
        "input_token_details": {"audio_tokens": 600, "cached_tokens": 400},
        "output_token_details": {"audio_tokens": 0},
    })
    assert one["audio_in"] == 200
    assert one["audio_cached"] == 400
    expected = (200 * 32.00 + 400 * 0.40) / 1_000_000
    assert one["usd"] == pytest.approx(expected)


def test_caching_makes_a_large_difference():
    """The same 600 tokens cost far less once cached — worth measuring."""
    fresh = Usage()
    fresh.add({"input_token_details": {"audio_tokens": 600},
               "output_token_details": {}})
    cached = Usage()
    cached.add({"input_token_details": {"audio_tokens": 600, "cached_tokens": 600},
                "output_token_details": {}})
    assert cached.usd < fresh.usd / 50


def test_detailed_cached_breakdown_is_preferred_when_present():
    u = Usage()
    one = u.add({
        "input_token_details": {
            "audio_tokens": 500,
            "text_tokens": 100,
            "cached_tokens": 400,
            "cached_tokens_details": {"audio_tokens": 300, "text_tokens": 100},
        },
        "output_token_details": {},
    })
    assert one["audio_in"] == 200      # 500 - 300 cached
    assert one["audio_cached"] == 300
    assert one["text_in"] == 0         # 100 - 100 cached
    assert one["text_cached"] == 100


def test_totals_accumulate_across_responses():
    u = Usage()
    for _ in range(3):
        u.add({"input_token_details": {"audio_tokens": 600},
               "output_token_details": {"audio_tokens": 1200}})
    assert u.responses == 3
    assert u.audio_in == 1800
    assert u.audio_out == 3600
    expected = (1800 * 32.00 + 3600 * 64.00) / 1_000_000
    assert u.usd == pytest.approx(expected)


def test_missing_or_empty_usage_does_not_crash():
    u = Usage()
    one = u.add({})
    assert one["usd"] == 0
    assert u.responses == 1


def test_cached_greater_than_input_cannot_go_negative():
    """Defensive: a malformed payload must not produce a negative bill."""
    u = Usage()
    one = u.add({
        "input_token_details": {"audio_tokens": 100, "cached_tokens": 500},
        "output_token_details": {},
    })
    assert one["audio_in"] == 0
    assert one["usd"] >= 0


def test_summary_reports_inr_and_a_token_total():
    u = Usage()
    u.add({"input_token_details": {"audio_tokens": 1000},
           "output_token_details": {"audio_tokens": 1000}})
    s = u.summary()
    assert s["total_tokens"] == 2000
    assert s["inr"] == pytest.approx(s["usd"] * USD_TO_INR, rel=1e-3)
    assert s["model"] == "gpt-realtime-2.1"


def test_mini_costs_about_a_third():
    counts = {"input_token_details": {"audio_tokens": 1000},
              "output_token_details": {"audio_tokens": 1000}}
    full = Usage(model="gpt-realtime-2.1")
    full.add(counts)
    mini = Usage(model="gpt-realtime-2.1-mini")
    mini.add(counts)
    assert mini.usd == pytest.approx(full.usd / 3.2, rel=0.01)
