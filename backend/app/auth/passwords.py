"""Password hashing (argon2id) and the rules for a new password.

The rules follow NIST SP 800-63B: a minimum length, no composition rules
("one capital, one symbol"), and a refusal of passwords everyone tries first.
"""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 8
MAX_LENGTH = 128

_hasher = PasswordHasher()

# Long enough to pass the length rule, common enough to be guessed first.
COMMON = frozenset({
    "password", "password1", "password12", "password123", "12345678", "123456789",
    "1234567890", "87654321", "11111111", "00000000", "qwertyui", "qwerty123",
    "abcd1234", "abc12345", "iloveyou", "admin123", "welcome1", "letmein1",
    "sunshine", "princess", "football", "baseball", "changeme",
    "123456789012", "1234567890123", "12345678901234", "123456123456",
    "password1234", "password12345", "passwordpassword", "password@123",
    "qwertyuiop12", "qwertyuiopasdf", "qwerty123456", "1q2w3e4r5t6y",
    "iloveyou1234", "welcome12345", "letmein12345", "admin1234567",
    "administrator", "abc123456789", "abcdefghijkl", "aaaaaaaaaaaa",
    "changeme1234", "000000000000", "111111111111", "987654321098",
    "translation1", "interpreter1", "softsuave123", "voice1234567",
})


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True once the hashing parameters have been strengthened since."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password-for-timing")


def burn_time(password: str) -> None:
    """Spend what a real check costs, so an unknown email is not faster."""
    verify_password(_dummy_hash(), password)


def problem_with(password: str, *, email: str = "", name: str = "") -> str | None:
    """Why `password` cannot be used, in words for the person choosing it."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    lowered = password.lower()
    if lowered in COMMON or len(set(lowered)) <= 2:
        return "This password is too common. Try a short phrase only you would use."
    local = email.split("@")[0].lower()
    if (local and lowered == local) or lowered == email.lower() or \
            (name and lowered == name.lower().replace(" ", "")):
        return "Don't use your name or email as the password."
    return None
