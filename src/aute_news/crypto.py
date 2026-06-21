"""대칭 암호화 (Fernet) — 테넌트 비밀번호(메일/CMS) 저장용.

키는 .env 의 CONFIG_ENC_KEY (Fernet 키). 키가 바뀌면 기존 암호문 복호화 불가.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.getenv("CONFIG_ENC_KEY")
        if not key:
            raise RuntimeError("CONFIG_ENC_KEY 가 .env 에 없습니다 (Fernet 키).")
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plain: str | None) -> str | None:
    if not plain:
        return None
    return _f().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _f().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
