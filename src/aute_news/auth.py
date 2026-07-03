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


def send_recovery_email(email: str, redirect_to: str | None = None) -> bool:
    """Supabase(GoTrue) 비밀번호 재설정 메일 발송.

    존재하지 않는 이메일이어도 GoTrue 는 200 을 돌려준다(계정 열거 방지). redirect_to 는
    메일 링크가 돌아올 우리 /reset-password URL(Supabase Auth 의 Redirect URLs 에 등록 필요).
    """
    if not SUPABASE_URL or not ANON_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_ANON_KEY 가 .env 에 없습니다.")
    try:
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/recover",
            headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
            params={"redirect_to": redirect_to} if redirect_to else None,
            json={"email": email}, timeout=15)
    except requests.RequestException:
        return False
    return r.status_code < 400


def tenant_for_user(conn, user_id: str) -> tuple[int | None, str | None]:
    """user_id(UUID) → (tenant_id, role). 미배정이면 (None, None)."""
    row = conn.execute(
        "SELECT tenant_id, role FROM tenant_users WHERE user_id=?", (user_id,)).fetchone()
    return (row["tenant_id"], row["role"]) if row else (None, None)
