"""Input validation and log redaction (design doc §13, §27)."""

import pytest
from pydantic import ValidationError

from app.logging_config import mask_phone
from app.schemas.call import StartCallRequest


def test_accepts_supported_language_pair():
    req = StartCallRequest(source_language="TA", target_language="hi",
                           phone_number="+91 87546 77067")
    assert req.source_language == "ta"
    assert req.phone_number == "+918754677067"


def test_rejects_unknown_language():
    with pytest.raises(ValidationError):
        StartCallRequest(source_language="klingon", target_language="hi")


@pytest.mark.parametrize("bad", ["918754677067", "+91-abc", "+0123456789", "12345", ""])
def test_rejects_malformed_phone_numbers(bad):
    with pytest.raises(ValidationError):
        StartCallRequest(source_language="ta", target_language="hi", phone_number=bad)


def test_phone_number_optional_for_demo_mode():
    req = StartCallRequest(source_language="ta", target_language="hi")
    assert req.phone_number is None


def test_phone_numbers_are_masked_in_logs():
    assert mask_phone("+918754677067") == "+********7067"
    assert mask_phone(None) == ""
    assert mask_phone("") == ""


def test_mask_never_leaks_anything_but_the_last_four():
    for number in ("+14155552671", "+918754677067", "+91 87546 77067"):
        masked = mask_phone(number)
        digits = "".join(c for c in number if c.isdigit())
        assert masked.endswith(digits[-4:])
        assert digits[:-4] not in masked
        assert sum(c.isdigit() for c in masked) == 4


def test_twilio_error_hints_cover_the_likely_failures():
    """The browser must get an actionable reason, not "call failed"."""
    from app.services.twilio_service import CALL_ERROR_HINTS

    # geo permissions is the single most likely cause for an India call
    assert 21215 in CALL_ERROR_HINTS
    assert "Geo Permissions" in CALL_ERROR_HINTS[21215]
    # unverified-destination on trial
    assert 21219 in CALL_ERROR_HINTS
    for code, hint in CALL_ERROR_HINTS.items():
        assert isinstance(code, int)
        assert hint and hint[0].isupper(), f"{code} hint should read as a sentence"


def test_call_origination_error_carries_a_safe_message():
    from app.services.twilio_service import CallOriginationError

    err = CallOriginationError(21215, "Enable India in Geo Permissions.")
    assert err.code == 21215
    assert "Geo Permissions" in err.message
