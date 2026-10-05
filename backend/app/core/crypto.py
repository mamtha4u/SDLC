"""Secrets vault primitives: Fernet with a master key kept outside the DB (data/master.key, chmod 600)."""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    path = get_settings().master_key_file
    if not path.exists():
        path.write_text(Fernet.generate_key().decode())
        path.chmod(0o600)
    return Fernet(path.read_text().strip().encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
