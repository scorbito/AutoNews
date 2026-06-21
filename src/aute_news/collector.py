"""IMAP 수집기 — UID 추적, 멀티테넌트.

테넌트별로 자기 메일 설정(tenant_config)을 읽어 수집한다(collect_for_tenant).
레거시 .env 단일계정 수집(collect_account/collect_all)도 유지(CLI/개발용).
모든 저장은 tenant_id 로 태깅된다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from imap_tools import AND, MailBox

from . import db, images
from .extractors import ExtractError, detect_format, extract_file

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
ATTACH_DIR = ROOT / "data" / "attachments"


def _folders(env_key: str, default: str) -> list[str]:
    raw = os.getenv(env_key, default)
    return [f.strip() for f in raw.split(",") if f.strip()]


ACCOUNTS = {
    "naver": {"host": "imap.naver.com",
              "email": os.getenv("NAVER_EMAIL"), "password": os.getenv("NAVER_PASSWORD"),
              "folders": _folders("NAVER_FOLDERS", "내게쓴메일함")},
    "daum": {"host": "imap.daum.net",
             "email": os.getenv("DAUM_EMAIL"), "password": os.getenv("DAUM_PASSWORD"),
             "folders": _folders("DAUM_FOLDERS", "INBOX")},
}


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


def _collect_mailbox(conn, tenant_id: int, account: str, host: str, email: str,
                     password: str, folders: list[str], batch_limit: int = 200) -> dict:
    """한 메일함을 UID 추적으로 수집(tenant_id 태깅). 핵심 루프."""
    stats = {"account": account, "new_messages": 0, "attachments": 0,
             "extracted": 0, "manual": 0}
    with MailBox(host).login(email, password) as mb:
        for folder in folders:
            mb.folder.set(folder)
            last_uid = db.get_last_uid(conn, account, folder, tenant_id=tenant_id)
            max_uid = last_uid
            for msg in mb.fetch(AND(uid=f"{last_uid + 1}:*"),
                                mark_seen=False, bulk=False, limit=batch_limit):
                uid = int(msg.uid)
                if uid <= last_uid:
                    continue
                max_uid = max(max_uid, uid)
                pk = db.insert_message(
                    conn, tenant_id=tenant_id, account=account, folder=folder, uid=uid,
                    message_id=msg.headers.get("message-id", (None,))[0] or f"{account}:{folder}:{uid}",
                    subject=msg.subject, sender=msg.from_, date=msg.date_str,
                    body_text=msg.text or msg.html or "")
                if pk is None:
                    continue
                stats["new_messages"] += 1

                for att in msg.attachments:
                    fmt = detect_format(att.filename)
                    dest = ATTACH_DIR / str(tenant_id) / account / f"{uid}_{_safe_name(att.filename)}"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(att.payload)
                    stats["attachments"] += 1

                    extracted_text, status, draft = None, "pending", None
                    try:
                        draft = extract_file(str(dest), att.filename)
                        extracted_text, status = draft.body_text, "done"
                        stats["extracted"] += 1
                    except ExtractError:
                        status = "manual"
                        stats["manual"] += 1

                    att_id = db.insert_attachment(
                        conn, tenant_id=tenant_id, message_pk=pk, filename=att.filename,
                        format=fmt, path=str(dest), size=len(att.payload),
                        extracted_text=extracted_text, extract_status=status)
                    if draft and draft.images:
                        images.process_images(conn, att_id, draft, tenant_id=tenant_id)

            db.set_last_uid(conn, account, folder, max_uid, tenant_id=tenant_id)
            conn.commit()
    return stats


def collect_for_tenant(tenant_id: int) -> dict:
    """테넌트 설정(tenant_config)의 메일함에서 수집."""
    conn = db.connect()
    cfg = db.get_tenant_config(conn, tenant_id)
    if not cfg or not (cfg.get("imap_host") and cfg.get("imap_email") and cfg.get("imap_password")):
        conn.close()
        return {"tenant_id": tenant_id, "skipped": "메일 설정 없음"}
    folders = [f.strip() for f in (cfg.get("imap_folders") or "INBOX").split(",") if f.strip()]
    try:
        stats = _collect_mailbox(conn, tenant_id, cfg["imap_email"], cfg["imap_host"],
                                 cfg["imap_email"], cfg["imap_password"], folders)
    finally:
        conn.close()
    stats["tenant_id"] = tenant_id
    return stats


# --- 레거시(.env 단일 계정, CLI/개발용) ---

def collect_account(name: str, folders: list[str] | None = None) -> dict:
    cfg = ACCOUNTS[name]
    if not cfg["email"] or not cfg["password"]:
        return {"account": name, "skipped": "no credentials"}
    conn = db.connect()
    try:
        return _collect_mailbox(conn, db.DEFAULT_TENANT, name, cfg["host"],
                                cfg["email"], cfg["password"],
                                folders or cfg.get("folders") or ["INBOX"])
    finally:
        conn.close()


def collect_all(targets: list[str] | None = None) -> list[dict]:
    return [collect_account(n) for n in (targets or list(ACCOUNTS))]
