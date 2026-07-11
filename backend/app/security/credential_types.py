"""Public credential references and one-time use-ticket contracts.

Credential plaintext is deliberately absent from these API/model-facing types.
The Electron main process owns encrypted password storage and ticket consumption.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_NONCE_PATTERN = r"^[A-Za-z0-9_-]{24,128}$"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CredentialContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialRef(CredentialContract):
    schema_version: Literal["credential-ref-v1"] = "credential-ref-v1"
    id: str = Field(min_length=8, max_length=128, pattern=_IDENTIFIER_PATTERN)
    domain: str = Field(min_length=1, max_length=253)
    kind: Literal["password"] = "password"
    created_at: str
    updated_at: str

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        normalized = value.strip().lower().rstrip(".")
        if not normalized or ":" in normalized or "/" in normalized or "@" in normalized:
            raise ValueError("credential domain must be an exact hostname")
        try:
            normalized = normalized.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("credential domain is invalid") from exc
        labels = normalized.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError("credential domain is invalid")
        return normalized

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        _parse_utc(value)
        return value


class CredentialUseTicket(CredentialContract):
    schema_version: Literal["credential-use-ticket-v1"] = "credential-use-ticket-v1"
    ticket_id: str = Field(min_length=8, max_length=128, pattern=_IDENTIFIER_PATTERN)
    credential_ref_id: str = Field(min_length=8, max_length=128, pattern=_IDENTIFIER_PATTERN)
    domain: str = Field(min_length=1, max_length=253)
    run_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    task_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    purpose: Literal["sign-in"] = "sign-in"
    issued_at: str
    expires_at: str
    nonce: str = Field(min_length=24, max_length=128, pattern=_NONCE_PATTERN)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return CredentialRef.validate_domain(value)

    @model_validator(mode="after")
    def validate_expiry(self) -> CredentialUseTicket:
        issued = _parse_utc(self.issued_at)
        expires = _parse_utc(self.expires_at)
        if expires <= issued:
            raise ValueError("credential use ticket expiry must follow issuance")
        if (expires - issued).total_seconds() > 120:
            raise ValueError("credential use ticket cannot exceed 120 seconds")
        return self
