"""Token-at-rest encryption and HMAC verification.

Pure domain logic — no FastAPI/SQLAlchemy imports, per the Clean Architecture
layering rule (Technical Blueprint Section 3.2).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from urllib.parse import parse_qsl

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class InvalidHmacSignatureError(Exception):
    """Raised when a Shopify HMAC signature fails constant-time verification."""


class TimestampExpiredError(Exception):
    """Raised when a request's timestamp falls outside the freshness window."""


def verify_shopify_hmac(query_params: dict[str, str], secret: str) -> None:
    """Validate a Shopify OAuth HMAC signature.

    Recomputes HMAC-SHA256 over the sorted query string (excluding the hmac
    parameter itself) and compares it against the supplied signature using
    hmac.compare_digest for constant-time equality, per Sprint 1 Security
    item 1 and the identical requirement stated in SATDD, the Technical
    Blueprint, and the API Contract Specification.
    """
    provided_hmac = query_params.get("hmac", "")
    if not provided_hmac:
        raise InvalidHmacSignatureError("Missing hmac parameter")

    filtered = {k: v for k, v in query_params.items() if k != "hmac"}
    message = "&".join(f"{key}={value}" for key, value in sorted(filtered.items()))

    computed_hmac = hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hmac, provided_hmac):
        raise InvalidHmacSignatureError("HMAC signature mismatch")


def verify_webhook_hmac(raw_body: bytes, hmac_header: str, secret: str) -> bool:
    """Validate a Shopify webhook HMAC signature (Shopify Compliance Webhooks:
    `app/uninstalled`, `customers/data_request`, `customers/redact`,
    `shop/redact`).

    This is a DIFFERENT signature scheme from `verify_shopify_hmac` (OAuth):
    Shopify signs the *raw JSON request body* (not the query string) with the
    app's client secret, base64-encodes the HMAC-SHA256 digest (not hex), and
    sends it in the `X-Shopify-Hmac-Sha256` request header. The raw body must
    be captured via `await request.body()` before any JSON parsing, since
    re-serializing a parsed payload is not guaranteed to byte-for-byte match
    what Shopify actually signed.

    Returns True/False rather than raising, so callers can respond 401
    without needing to catch an exception for what is, on this ingress path,
    an expected/routine outcome (Shopify does send probe/test deliveries).
    """
    if not hmac_header:
        return False

    computed_digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    computed_hmac = base64.b64encode(computed_digest).decode("ascii")

    return hmac.compare_digest(computed_hmac, hmac_header)


def verify_timestamp_freshness(
    request_timestamp: int, current_timestamp: int, drift_seconds: int = 300
) -> None:
    """Reject requests whose timestamp has drifted beyond the allowed window.

    Risk 1 mitigation: "Allow a configurable 300-second timestamp drift window."
    """
    if abs(current_timestamp - request_timestamp) > drift_seconds:
        raise TimestampExpiredError(
            f"Timestamp drift {abs(current_timestamp - request_timestamp)}s "
            f"exceeds allowed {drift_seconds}s window"
        )


def sorted_query_string(query_params: dict[str, str]) -> str:
    """Helper kept for callers that need the canonical signed string."""
    filtered = {k: v for k, v in query_params.items() if k != "hmac"}
    return "&".join(f"{key}={value}" for key, value in sorted(filtered.items()))


def parse_query_string(query_string: str) -> dict[str, str]:
    return dict(parse_qsl(query_string, keep_blank_values=True))


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext_b64: str
    nonce_b64: str

    def serialize(self) -> str:
        """Single-string storage format for the `access_token_encrypted` column."""
        return f"{self.nonce_b64}:{self.ciphertext_b64}"

    @classmethod
    def deserialize(cls, value: str) -> "EncryptedPayload":
        nonce_b64, ciphertext_b64 = value.split(":", 1)
        return cls(ciphertext_b64=ciphertext_b64, nonce_b64=nonce_b64)


class TokenCipher:
    """AES-256-GCM envelope encryption for Shopify access tokens.

    `key_material` is a 32-byte key resolved by the caller. In production this
    key is retrieved from AWS KMS / GCP KMS (see KMS_KEY_ID in config); the
    KMS integration itself is an infrastructure concern that plugs in here via
    `from_kms_key_id` in a later sprint. For local/dev/test, a base64-encoded
    key is read directly from NIGHTSHIFT_LOCAL_DATA_KEY so Sprint 1 does not
    require live cloud KMS access to run.
    """

    def __init__(self, key_material: bytes) -> None:
        if len(key_material) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        self._aesgcm = AESGCM(key_material)

    @classmethod
    def from_base64_key(cls, base64_key: str) -> "TokenCipher":
        if not base64_key:
            raise ValueError(
                "NIGHTSHIFT_LOCAL_DATA_KEY is not set. Generate one with: "
                "python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
            )
        return cls(base64.b64decode(base64_key))

    def encrypt(self, plaintext: str) -> EncryptedPayload:
        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return EncryptedPayload(
            ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
            nonce_b64=base64.b64encode(nonce).decode("ascii"),
        )

    def decrypt(self, payload: EncryptedPayload) -> str:
        nonce = base64.b64decode(payload.nonce_b64)
        ciphertext = base64.b64decode(payload.ciphertext_b64)
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
