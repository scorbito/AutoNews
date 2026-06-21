"""인증 — Supabase Auth(GoTrue) 로그인 + 사용자→테넌트 매핑.

서버사이드 로그인: 이메일/비번을 Supabase GoTrue 로 검증하고,
성공 시 우리 세션 쿠키에 user_id/email/tenant_id 를 저장한다.
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")


def supabase_login(email: str, password: str) -> dict | None:
    """GoTrue 비밀번호 로그인. 성공 시 {'id','email'}, 실패 시 None."""
    if not SUPABASE_URL or not ANON_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_ANON_KEY 가 .env 에 없습니다.")
    try:
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password}, timeout=15)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    u = r.json().get("user", {})
    return {"id": u.get("id"), "email": u.get("email")} if u.get("id") else None


def tenant_for_user(conn, user_id: str) -> tuple[int | None, str | None]:
    """user_id(UUID) → (tenant_id, role). 미배정이면 (None, None)."""
    row = conn.execute(
        "SELECT tenant_id, role FROM tenant_users WHERE user_id=?", (user_id,)).fetchone()
    return (row["tenant_id"], row["role"]) if row else (None, None)
