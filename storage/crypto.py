"""Application-level encryption for values stored in Redis (AES-256-GCM).

Kept deliberately small so it can later be replaced by KMS / envelope
encryption without touching the views.
"""

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

ENCRYPTION_VERSION = "v1"
KEY_SIZE_BYTES = 32  # AES-256
NONCE_SIZE_BYTES = 12  # 96-bit nonce, the recommended size for GCM


class DecryptionError(Exception):
    """The payload could not be authenticated (tampered data, wrong nonce or AAD)."""


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext: bytes  # includes the 16-byte GCM authentication tag
    nonce: bytes


def decode_key(encoded_key):
    """Decode and validate STORAGE_ENCRYPTION_KEY. There is no fallback key."""
    if not encoded_key or not encoded_key.strip():
        raise ImproperlyConfigured(
            "STORAGE_ENCRYPTION_KEY is not set. It must be a Base64-encoded 32-byte key "
            "(see README: 'Encryption key')."
        )
    try:
        key = base64.b64decode(encoded_key.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ImproperlyConfigured("STORAGE_ENCRYPTION_KEY is not valid Base64.") from None
    if len(key) != KEY_SIZE_BYTES:
        raise ImproperlyConfigured(
            f"STORAGE_ENCRYPTION_KEY must decode to exactly {KEY_SIZE_BYTES} bytes "
            f"(got {len(key)})."
        )
    return key


def encrypt(data: bytes, aad: bytes) -> EncryptedPayload:
    # A fresh random nonce per value: never reused with the same key.
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(settings.STORAGE_ENCRYPTION_KEY).encrypt(nonce, data, aad)
    return EncryptedPayload(ciphertext=ciphertext, nonce=nonce)


def decrypt(ciphertext: bytes, nonce: bytes, aad: bytes) -> bytes:
    try:
        return AESGCM(settings.STORAGE_ENCRYPTION_KEY).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError, TypeError):
        raise DecryptionError() from None
