from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.security.credential_types import CredentialRef, CredentialUseTicket


def test_credential_contracts_expose_metadata_without_plaintext() -> None:
    ref = CredentialRef(
        id="cred_12345678",
        domain="EXAMPLE.TEST.",
        created_at="2026-07-11T00:00:00Z",
        updated_at="2026-07-11T00:00:00Z",
    )
    ticket = CredentialUseTicket(
        ticket_id="ticket_12345678",
        credential_ref_id=ref.id,
        domain=ref.domain,
        run_id="run_1",
        task_id="task_1",
        issued_at="2026-07-11T00:00:00Z",
        expires_at="2026-07-11T00:01:00Z",
        nonce="nonce_123456789012345678901234",
    )

    assert ref.domain == "example.test"
    assert "password" not in ticket.model_dump()
    assert "username" not in ticket.model_dump()


def test_credential_contracts_reject_plaintext_and_long_lived_tickets() -> None:
    with pytest.raises(ValidationError):
        CredentialRef.model_validate(
            {
                "id": "cred_12345678",
                "domain": "https://example.test",
                "created_at": "2026-07-11T00:00:00Z",
                "updated_at": "2026-07-11T00:00:00Z",
                "password": "must-not-cross-boundary",
            }
        )

    with pytest.raises(ValidationError, match="120 seconds"):
        CredentialUseTicket(
            ticket_id="ticket_12345678",
            credential_ref_id="cred_12345678",
            domain="example.test",
            run_id="run_1",
            task_id="task_1",
            issued_at="2026-07-11T00:00:00Z",
            expires_at="2026-07-11T00:03:00Z",
            nonce="nonce_123456789012345678901234",
        )
