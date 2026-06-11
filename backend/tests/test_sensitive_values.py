"""Tests for app.policy.sensitive_values (browser write-value gating)."""

from __future__ import annotations

import pytest

from app.policy.sensitive_values import _luhn_valid, looks_sensitive_value


class TestLooksSensitiveValueNegative:
    def test_none_is_not_sensitive(self):
        assert looks_sensitive_value(None) is False

    def test_empty_string_is_not_sensitive(self):
        assert looks_sensitive_value("") is False

    @pytest.mark.parametrize(
        "value",
        [
            "hello world",
            "搜索今天的天气",
            "user@example.com",
            "short-token",
            # 12 digits: too short for card-like Luhn match.
            "123456789012",
            # Card-like length but fails Luhn.
            "4111 1111 1111 1112",
            # Phone number formats stay usable.
            "138-0013-8000",
        ],
    )
    def test_ordinary_values_pass(self, value: str):
        assert looks_sensitive_value(value) is False


class TestLooksSensitiveValuePositive:
    @pytest.mark.parametrize(
        "value",
        [
            "api_key=abcdefgh12345678",
            "API-KEY: 'abcd1234efgh5678'",
            "password = hunter2hunter2",
            "Authorization: Bearer abc123def456ghi789",
            "Bearer abcdef1234567890",
            "sk-abcdefgh12345678",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
            # Generic high-entropy token (24+ chars of token alphabet).
            "ghp_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7x",
        ],
    )
    def test_secret_shaped_values_are_flagged(self, value: str):
        assert looks_sensitive_value(value) is True

    @pytest.mark.parametrize(
        "card",
        [
            "4111111111111111",  # Visa test number, Luhn-valid
            "4111 1111 1111 1111",
            "4111-1111-1111-1111",
            "5500005555555559",  # Mastercard test number
        ],
    )
    def test_luhn_valid_card_numbers_are_flagged(self, card: str):
        assert looks_sensitive_value(card) is True

    def test_card_number_embedded_in_text(self):
        assert looks_sensitive_value("ship to card 4111 1111 1111 1111 thanks") is True

    def test_non_string_value_is_coerced(self):
        assert looks_sensitive_value(4111111111111111) is True


class TestLuhn:
    @pytest.mark.parametrize("digits", ["4111111111111111", "5500005555555559", "379354508162306"])
    def test_valid(self, digits: str):
        assert _luhn_valid(digits) is True

    @pytest.mark.parametrize(
        "digits",
        [
            "4111111111111112",  # checksum off by one
            "411111111111",  # too short (12)
            "41111111111111111111",  # too long (20)
            "",
        ],
    )
    def test_invalid(self, digits: str):
        assert _luhn_valid(digits) is False
