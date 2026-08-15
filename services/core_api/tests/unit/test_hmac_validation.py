"""Unit tests: HMAC signature validation (Sprint 1 Section 1.19).

Named tests required by Sprint 1's Testing section:
  - test_hmac_validation_success
  - test_hmac_validation_tampered
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.domain.security import InvalidHmacSignatureError, verify_shopify_hmac

SECRET = "test-shopify-app-secret"


def _sign(params: dict[str, str], secret: str = SECRET) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_hmac_validation_success():
    params = {"shop": "acme.myshopify.com", "code": "abc123", "timestamp": "1785456000"}
    signed_params = {**params, "hmac": _sign(params)}

    # Should not raise.
    verify_shopify_hmac(signed_params, SECRET)


def test_hmac_validation_tampered():
    params = {"shop": "acme.myshopify.com", "code": "abc123", "timestamp": "1785456000"}
    signed_params = {**params, "hmac": _sign(params)}

    # Tamper with a signed field after the signature was computed.
    signed_params["shop"] = "attacker.myshopify.com"

    with pytest.raises(InvalidHmacSignatureError):
        verify_shopify_hmac(signed_params, SECRET)


def test_hmac_validation_missing_hmac_param():
    with pytest.raises(InvalidHmacSignatureError):
        verify_shopify_hmac({"shop": "acme.myshopify.com"}, SECRET)


def test_hmac_validation_wrong_secret():
    params = {"shop": "acme.myshopify.com", "code": "abc123"}
    signed_params = {**params, "hmac": _sign(params, secret=SECRET)}

    with pytest.raises(InvalidHmacSignatureError):
        verify_shopify_hmac(signed_params, secret="a-different-secret")
