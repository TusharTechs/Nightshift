"""Unit tests: Shopify webhook HMAC verification (`verify_webhook_hmac`).

Distinct scheme from `verify_shopify_hmac` (tested in
test_hmac_validation.py) — signs the raw request body, base64-encodes the
digest, and is delivered via the `X-Shopify-Hmac-Sha256` header rather than
a query-string `hmac` parameter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from app.domain.security import verify_webhook_hmac

SECRET = "test-shopify-app-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_webhook_hmac_valid_signature_accepted():
    body = b'{"myshopify_domain": "acme.myshopify.com"}'
    header = _sign(body)

    assert verify_webhook_hmac(body, header, SECRET) is True


def test_webhook_hmac_tampered_body_rejected():
    body = b'{"myshopify_domain": "acme.myshopify.com"}'
    header = _sign(body)
    tampered_body = b'{"myshopify_domain": "attacker.myshopify.com"}'

    assert verify_webhook_hmac(tampered_body, header, SECRET) is False


def test_webhook_hmac_wrong_secret_rejected():
    body = b'{"myshopify_domain": "acme.myshopify.com"}'
    header = _sign(body, secret=SECRET)

    assert verify_webhook_hmac(body, header, "a-different-secret") is False


def test_webhook_hmac_missing_header_rejected():
    body = b'{"myshopify_domain": "acme.myshopify.com"}'

    assert verify_webhook_hmac(body, "", SECRET) is False


def test_webhook_hmac_hex_instead_of_base64_rejected():
    """A caller that (incorrectly) sent the OAuth-style hex digest instead of
    base64 must still be rejected — proves the two HMAC schemes are not
    accidentally interchangeable."""
    body = b'{"myshopify_domain": "acme.myshopify.com"}'
    hex_digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_webhook_hmac(body, hex_digest, SECRET) is False
