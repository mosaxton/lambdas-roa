"""AES-256-GCM encryption matching the TypeScript web app's lib/encryption.ts.

Byte layout (non-negotiable, must match TS exactly):
    [IV (12 bytes)] [authTag (16 bytes)] [ciphertext (N bytes)]

Total encrypted length = 28 + len(plaintext_utf8_bytes).
"""

import os
import re
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IV_LENGTH: int = 12
AUTH_TAG_LENGTH: int = 16
MIN_ENCRYPTED_LENGTH: int = IV_LENGTH + AUTH_TAG_LENGTH  # 28


def _get_key() -> bytes:
    """Load and validate the AES key from the ENCRYPTION_KEY env var.

    Returns the raw 32-byte key.
    Raises ValueError if the env var is absent or malformed.
    Never logs or exposes the key value.
    """
    hex_key = os.environ.get("ENCRYPTION_KEY")
    if not hex_key:
        raise ValueError("ENCRYPTION_KEY environment variable is not set")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", hex_key):
        raise ValueError("ENCRYPTION_KEY must be a 64-character hex string (32 bytes)")
    return bytes.fromhex(hex_key)


def encrypt(plaintext: str) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM.

    Returns bytes with layout: iv (12) + auth_tag (16) + ciphertext (N).
    A fresh IV is generated for every call.
    """
    key = _get_key()
    iv = secrets.token_bytes(IV_LENGTH)
    aesgcm = AESGCM(key)
    # cryptography returns ciphertext || auth_tag (tag appended at the end)
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # Split: last AUTH_TAG_LENGTH bytes are the tag, the rest is ciphertext
    ciphertext = ciphertext_with_tag[:-AUTH_TAG_LENGTH]
    auth_tag = ciphertext_with_tag[-AUTH_TAG_LENGTH:]
    # Reorder to match TS layout: iv + auth_tag + ciphertext
    return iv + auth_tag + ciphertext


def decrypt(data: bytes) -> str:
    """Decrypt *data* produced by encrypt() or the TypeScript equivalent.

    Raises ValueError if data is too short or the auth tag is invalid.
    Returns the original plaintext string.
    """
    if len(data) < MIN_ENCRYPTED_LENGTH:
        raise ValueError("Invalid encrypted value: buffer too short")
    key = _get_key()
    iv = data[:IV_LENGTH]
    auth_tag = data[IV_LENGTH : IV_LENGTH + AUTH_TAG_LENGTH]
    ciphertext = data[IV_LENGTH + AUTH_TAG_LENGTH :]
    aesgcm = AESGCM(key)
    # Reconstruct the library's expected ciphertext || tag layout
    plaintext_bytes = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
    return plaintext_bytes.decode("utf-8")
