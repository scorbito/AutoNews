"""관리자(SaaS 운영자) 기능 — 신문사 온보딩.

- 관리자 식별: .env ADMIN_EMAILS (쉼표구분 이메일)
- 신문사 계정 생성(Supabase Auth user + tenant + 매핑)
- 테넌트 목록/요약, 테넌트 수집·처리 실행
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

from . import db, pipeline, subscription
from .collector import collect_for_tenant

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}


def is_admin(email: str | None) -> bool:
    return bool(email) and email.lower() in ADMIN_EMAILS


def _create_supabase_user(email: str, password: str) -> str:
    if not SUPABASE_URL or not SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY 필요")
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
                 "Content-Type": "application/json"},
        json={"email": email, "password": password, "email_confirm": True}, timeout=20)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"사용자 생성 실패({r.status_code}): {r.text[:150]}")
    return r.json()["id"]


def add_user(conn, tenant_id: int, email: str, password: str) -> str:
    """기존 신문사에 기자(사용자) 추가. user_id 반환."""
    uid = _create_supabase_user(email, password)
    conn.execute(
        """INSERT INTO tenant_users (user_id, tenant_id, email, role) VALUES (?,?,?,'editor')
           ON CONFLICT (user_id) DO UPDATE SET tenant_id=excluded.tenant_id""",
        (uid, tenant_id, email))
    return uid


def create_account(conn, name: str, email: str, password: str) -> tuple[int, str]:
    """기자 테넌트 + Supabase Auth 사용자 생성 + 매핑 + 무료 체험 구독. (tenant_id, user_id) 반환."""
    tid = conn.execute("INSERT INTO tenants (name) VALUES (?) RETURNING id", (name,)).fetchone()["id"]
    uid = add_user(conn, tid, email, password)
    subscription.start_trial(conn, tid)   # 가입 즉시 무료 체험 시작
    return tid, uid


def list_tenants(conn) -> list[dict]:
    """테넌트 목록 + 설정·기사수 요약."""
    rows = conn.execute("SELECT id, name, slug, status FROM tenants ORDER BY id").fetchall()
    out = []
    for t in rows:
        cfg = db.get_tenant_config(conn, t["id"]) or {}
        arts = conn.execute("SELECT COUNT(*) c FROM articles WHERE tenant_id=?", (t["id"],)).fetchone()["c"]
        urows = conn.execute(
            "SELECT user_id, email, role FROM tenant_users WHERE tenant_id=? ORDER BY created_at",
            (t["id"],)).fetchall()
        users = []
        for u in urows:
            m = db.get_user_mail(conn, u["user_id"]) or {}
            users.append({
                "user_id": u["user_id"], "email": u["email"], "role": u["role"],
                "imap_email": m.get("imap_email"),
                "imap_folders": m.get("imap_folders") or "",
                "has_mail": bool(m.get("imap_host") and m.get("imap_email")),
                "collect_enabled": bool(m.get("collect_enabled")),
            })
        sub = subscription.status_view(conn, t["id"])
        out.append({
            "id": t["id"], "name": t["name"], "status": t["status"],
            "emails": [u["email"] for u in users],
            "users": users,
            "imap_email": cfg.get("imap_email"), "publisher": cfg.get("publisher"),
            "pipeline_mode": cfg.get("pipeline_mode"), "articles": arts,
            "has_mail": bool(cfg.get("imap_host") and cfg.get("imap_email")),
            "collect_enabled": cfg.get("collect_enabled"),
            "collect_times": cfg.get("collect_times"),
            "cms_user_email": cfg.get("cms_user_email"),
            "cms_auto_submit": cfg.get("cms_auto_submit"),
            "sub": sub,
        })
    return out


def process_tenant(conn, tenant_id: int) -> int:
    """해당 테넌트의 미처리 메일을 파이프라인 처리(테넌트 발행모드 적용). 생성 기사 수 반환."""
    cfg = db.get_tenant_config(conn, tenant_id) or {}
    mode = cfg.get("pipeline_mode") or "review"
    rows = conn.execute(
        """SELECT m.id FROM messages m
           WHERE m.tenant_id=? AND m.archived_at IS NULL AND NOT EXISTS (
               SELECT 1 FROM articles ar JOIN attachments a ON a.id=ar.attachment_id
               WHERE a.message_pk=m.id)
           ORDER BY m.id""", (tenant_id,)).fetchall()
    made = 0
    for r in rows:
        # 결제주기 한도 소진 시 자동 생성 중단(남은 메일은 다음 주기로)
        if not subscription.can_use(conn, tenant_id)[0]:
            break
        res = pipeline.process_message(conn, r["id"], mode=mode, tenant_id=tenant_id)
        made += len(res.get("articles", []))
    return made


def collect_tenant(tenant_id: int) -> dict:
    return collect_for_tenant(tenant_id)


def _delete_supabase_user(user_id: str) -> None:
    if not (SUPABASE_URL and SERVICE_KEY and user_id):
        return
    try:
        requests.delete(
            f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def delete_tenant(conn, tenant_id: int) -> dict:
    """신문사(테넌트) + 모든 데이터 + Auth 사용자 + 스토리지 파일 삭제."""
    from .storage import get_storage
    keys, user_ids = db.delete_tenant(conn, tenant_id)
    for uid in user_ids:
        _delete_supabase_user(uid)
    storage = get_storage()
    files = 0
    for k in keys:
        try:
            storage.delete(k)
            files += 1
        except Exception:  # noqa: BLE001
            pass
    return {"files": files, "users": len(user_ids)}
