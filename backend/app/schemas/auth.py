"""Request bodies for signing in and asking for access."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Deliberately loose: one @, something on each side, a dot in the domain.
# Whether the address really exists is the admin's call when approving.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.match(value):
        raise ValueError("Enter a valid email address.")
    return value


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().lower()


class AccessRequest(BaseModel):
    # No role here, and extra fields are ignored: nobody can ask to be an admin.
    name: str = Field(max_length=100)
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Enter your name.")
        return value

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        return _email(value)
