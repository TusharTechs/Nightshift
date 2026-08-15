"""Unit tests: `shop` query parameter validation (Sprint 1 Endpoint 1)."""

from __future__ import annotations

import pytest

from app.application.use_cases.complete_oauth_installation import validate_shop_domain


@pytest.mark.parametrize(
    "shop",
    ["acme.myshopify.com", "acme-store-123.myshopify.com", "a.myshopify.com"],
)
def test_valid_shop_domains(shop: str):
    assert validate_shop_domain(shop) is True


@pytest.mark.parametrize(
    "shop",
    [
        "acme.evil.com",
        "https://acme.myshopify.com",
        "-acme.myshopify.com",
        "acme.myshopify.com.evil.com",
        "",
    ],
)
def test_invalid_shop_domains(shop: str):
    assert validate_shop_domain(shop) is False
