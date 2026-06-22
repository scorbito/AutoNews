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
from .extractors import ExtractError, detect_format, extract_bytes
from .storage import get_storage, mime_for

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


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


# 도메인 → IMAP 호스트 추정 (UI 자동완성용)
_IMAP_HOSTS = {
    "naver.com": "imap.naver.com",
    "daum.net": "imap.daum.net",
    "hanmail.net": "imap.daum.net",
    "gmail.com": "imap.gmail.com",
    "nate.com": "imap.mail.nate.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
}


def host_for_email(email: str) -> str:
    """이메일 도메인으로 IMAP 호스트 추정(모르면 빈 문자열)."""
    domain = (email or "").split("@")[-1].strip().lower()
    return _IMAP_HOSTS.get(domain, "")


# IMAP 시스템 메일함 → 네이버·다음식 한글 표시명.
# (수집엔 IMAP 진짜 이름을 쓰고, 화면 라벨만 한글로 맞춘다)
_SPECIAL_LABELS = {  # SPECIAL-USE 플래그 우선(메일사 공통)
    "\\Sent": "보낸메일함", "\\Drafts": "임시보관함", "\\Trash": "휴지통",
    "\\Junk": "스팸메일함", "\\Archive": "보관함", "\\All": "전체메일",
    "\\Flagged": "중요메일함",
}
_NAME_LABELS = {  # 플래그가 없을 때 표준 영문 이름으로 폴백
    "INBOX": "받은메일함", "Sent Messages": "보낸메일함", "Sent": "보낸메일함",
    "Drafts": "임시보관함", "Deleted Messages": "휴지통", "Trash": "휴지통",
    "Junk": "스팸메일함", "Spam": "스팸메일함", "Bulk Mail": "스팸메일함",
}


def _folder_label(name: str, flags) -> str:
    for fl in flags or ():
        if fl in _SPECIAL_LABELS:
            return _SPECIAL_LABELS[fl]
    return _NAME_LABELS.get(name, name)


def list_imap_folders(host: str, email: str, password: str) -> list[dict]:
    """계정 로그인 → 메일함 목록. [{name(IMAP 실제), label(한글 표시)}]."""
    with MailBox(host).login(email, password) as mb:
        return [{"name": f.name, "label": _folder_label(f.name, getattr(f, "flags", ()))}
                for f in mb.folder.list()]


def _collect_mailbox(conn, tenant_id: int, account: str, host: str, email: str,
                     password: str, folders: list[str], batch_limit: int = 200) -> dict:
    """한 메일함을 UID 추적으로 수집(tenant_id 태깅). 핵심 루프."""
    stats = {"account": account, "new_messages": 0, "attachments": 0,
             "extracted": 0, "manual": 0, "baselined": 0}
    with MailBox(host).login(email, password) as mb:
        for folder in folders:
            mb.folder.set(folder)
            # 최초 수집("지금부터"): 기준선이 없으면 현재 최대 UID를 저장하고
            # 과거 메일은 건너뜀 → 이후 도착하는 새 메일만 수집한다.
            if not db.folder_initialized(conn, account, folder, tenant_id=tenant_id):
                baseline = max((int(u) for u in mb.uids()), default=0)
                db.set_last_uid(conn, account, folder, baseline, tenant_id=tenant_id)
                conn.commit()
                stats["baselined"] += 1
                continue
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
                    key = f"attachments/{tenant_id}/{account}/{uid}_{_safe_name(att.filename)}"
                    get_storage().put(key, att.payload, mime_for(fmt))
                    stats["attachments"] += 1

                    extracted_text, status, draft = None, "pending", None
                    try:
                        draft = extract_bytes(att.payload, att.filename)
                        extracted_text, status = draft.body_text, "done"
                        stats["extracted"] += 1
                    except ExtractError:
                        status = "manual"
                        stats["manual"] += 1

                    att_id = db.insert_attachment(
                        conn, tenant_id=tenant_id, message_pk=pk, filename=att.filename,
                        format=fmt, path=key, size=len(att.payload),
                        extracted_text=extracted_text, extract_status=status)
                    if draft and draft.images:
                        images.process_images(conn, att_id, draft, tenant_id=tenant_id)

            db.set_last_uid(conn, account, folder, max_uid, tenant_id=tenant_id)
            conn.commit()
    return stats


def _collect_one_account(conn, tenant_id: int, mail: dict) -> dict:
    """기자 메일 설정(dict) 1건 수집. account 키는 이메일(기자별 격리)."""
    folders = [f.strip() for f in (mail.get("imap_folders") or "INBOX").split(",") if f.strip()]
    return _collect_mailbox(conn, tenant_id, mail["imap_email"], mail["imap_host"],
                            mail["imap_email"], mail["imap_password"], folders)


def collect_for_user(user_id: str) -> dict:
    """기자 본인 메일함에서 수집(user_mail_config)."""
    conn = db.connect()
    mail = db.get_user_mail(conn, user_id)
    if not mail or not (mail.get("imap_host") and mail.get("imap_email") and mail.get("imap_password")):
        conn.close()
        return {"user_id": user_id, "skipped": "메일 설정 없음"}
    try:
        stats = _collect_one_account(conn, mail["tenant_id"], mail)
    finally:
        conn.close()
    stats["user_id"] = user_id
    return stats


def collect_for_tenant(tenant_id: int, only_enabled: bool = False) -> dict:
    """신문사 소속 기자들의 메일함에서 수집(기자별 개인 계정).

    user_mail_config 가 있으면 그것을 쓰고, 하나도 없으면 레거시 tenant_config 로 폴백.
    """
    conn = db.connect()
    accounts = [m for m in db.list_tenant_mail_accounts(conn, tenant_id, only_enabled)
                if m.get("imap_host") and m.get("imap_email") and m.get("imap_password")]
    try:
        if accounts:
            per = []
            agg = {"tenant_id": tenant_id, "accounts": 0, "new_messages": 0,
                   "attachments": 0, "extracted": 0, "manual": 0, "baselined": 0}
            for m in accounts:
                s = _collect_one_account(conn, tenant_id, m)
                agg["accounts"] += 1
                for k in ("new_messages", "attachments", "extracted", "manual", "baselined"):
                    agg[k] += s.get(k, 0)
                per.append(s)
            agg["per_account"] = per
            return agg
        # 폴백: 레거시 테넌트 단위 메일 설정
        cfg = db.get_tenant_config(conn, tenant_id)
        if not cfg or not (cfg.get("imap_host") and cfg.get("imap_email") and cfg.get("imap_password")):
            return {"tenant_id": tenant_id, "skipped": "메일 설정 없음"}
        folders = [f.strip() for f in (cfg.get("imap_folders") or "INBOX").split(",") if f.strip()]
        stats = _collect_mailbox(conn, tenant_id, cfg["imap_email"], cfg["imap_host"],
                                 cfg["imap_email"], cfg["imap_password"], folders)
        stats["tenant_id"] = tenant_id
        return stats
    finally:
        conn.close()


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
