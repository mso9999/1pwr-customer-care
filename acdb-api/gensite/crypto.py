"""
Fernet-based encryption helper for gensite credential storage.

Key management:
    CC_CREDENTIAL_ENCRYPTION_KEY  — 44-char URL-safe base64 Fernet key.
    Generate one with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Store on the CC host in /opt/1pdb/.env (never in git, never in 1PDB).

Design choices:
    - Lazy key load: we only require the key when something tries to
      encrypt/decrypt, so the rest of the API still boots if the env var
      is missing (router endpoints will 503 with a clear message).
    - ciphertext is bytes; psycopg2 maps it to PostgreSQL bytea on write
      and returns memoryview on read. Both are handled transparently.
    - Rotation SOP: docs/ops/gensite-credentials.md.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("cc-api.gensite.crypto")


class CredentialCryptoError(RuntimeError):
    """Raised when the Fernet key is missing, malformed, or decryption fails."""


_ENV_VAR = "CC_CREDENTIAL_ENCRYPTION_KEY"

_cached_fernet: Optional[Fernet] = None


def _load_fernet() -> Fernet:
    global _cached_fernet
    if _cached_fernet is not None:
        return _cached_fernet

    raw = os.environ.get(_ENV_VAR, "").strip()
    if not raw:
        raise CredentialCryptoError(
            f"{_ENV_VAR} is not set on the CC host. "
            "Gensite credential storage requires a Fernet key. "
            "Generate with `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add to /opt/1pdb/.env."
        )
    try:
        _cached_fernet = Fernet(raw.encode() if isinstance(raw, str) else raw)
    except (ValueError, TypeError) as exc:
        raise CredentialCryptoError(
            f"{_ENV_VAR} is set but not a valid Fernet key: {exc}"
        ) from exc
    return _cached_fernet


def encrypt(plaintext: Optional[str]) -> Optional[bytes]:
    """Encrypt a string. Empty/None → None (store NULL)."""
    if plaintext is None or plaintext == "":
        return None
    return _load_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: Optional[object]) -> Optional[str]:
    """Decrypt Fernet ciphertext. Accepts bytes/memoryview; None → None."""
    if ciphertext is None:
        return None
    if isinstance(ciphertext, memoryview):
        ciphertext = ciphertext.tobytes()
    if isinstance(ciphertext, str):
        ciphertext = ciphertext.encode("utf-8")
    if not ciphertext:
        return None
    try:
        return _load_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError(
            "Stored credential ciphertext could not be decrypted. "
            "This usually means CC_CREDENTIAL_ENCRYPTION_KEY was rotated "
            "without re-encrypting existing rows."
        ) from exc


def mask(plaintext: Optional[str]) -> str:
    """Return a display-safe masked representation (never reveals the secret)."""
    if not plaintext:
        return ""
    if len(plaintext) <= 4:
        return "•" * len(plaintext)
    return plaintext[0] + "•" * (len(plaintext) - 2) + plaintext[-1]


def key_is_configured() -> bool:
    """Non-raising probe used by the /healthz-style endpoint."""
    try:
        _load_fernet()
        return True
    except CredentialCryptoError:
        return False
