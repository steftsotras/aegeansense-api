"""
Symmetric encryption for FINoT profile passwords at rest.

Uses Fernet (reversible) rather than a one-way hash, since the app needs
the plaintext password to authenticate against FINoT on each sync.
"""
from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

settings = get_settings()

if not settings.credential_encryption_key:
    raise RuntimeError(
        "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

_fernet = Fernet(settings.credential_encryption_key.encode())


def encrypt_password(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Could not decrypt stored FINoT password - CREDENTIAL_ENCRYPTION_KEY may have changed")
