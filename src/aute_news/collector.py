"""IMAP 수집기 — UID 추적 기반(읽음 상태 비침습).

설계 원칙:
  - 읽음/안읽음 플래그에 의존하지 않는다(사용자와 상태 공유 → 누락/간섭).
  - 폴더별 last_uid 를 저장하고 'UID > last_uid' 인 새 메일만 가져온다.
  - mark_seen=False 로 사용자 메일함의 읽음 상태를 건드리지 않는다.
  - message_id UNIQUE 로 이중 중복 방지.
첨부는 디스크에 저장하고, 지원 형식(HWP 등)은 즉시 추출 시도한다.
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
    """쉼표로 구분된 폴더 목록을 .env 에서 읽기(없으면 기본값)."""
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


def collect_account(name: str, folders: list[str] | None = None, batch_limit: int = 200) -> dict:
    cfg = ACCOUNTS[name]
    if not cfg["email"] or not cfg["password"]:
        return {"account": name, "skipped": "no credentials"}

    folders = folders or cfg.get("folders") or ["INBOX"]
    conn = db.connect()
    stats = {"account": name, "new_messages": 0, "attachments": 0, "extracted": 0, "manual": 0}

    with MailBox(cfg["host"]).login(cfg["email"], cfg["password"]) as mb:
        for folder in folders:
            mb.folder.set(folder)
            last_uid = db.get_last_uid(conn, name, folder)
            max_uid = last_uid
            # 'UID > last_uid' 만. (IMAP 특성상 N:* 는 최소 1건 반환 → 아래서 uid 재확인)
            criteria = AND(uid=f"{last_uid + 1}:*")
            for msg in mb.fetch(criteria, mark_seen=False, bulk=False, limit=batch_limit):
                uid = int(msg.uid)
                if uid <= last_uid:
                    continue
                max_uid = max(max_uid, uid)

                pk = db.insert_message(
                    conn, account=name, folder=folder, uid=uid,
                    message_id=msg.headers.get("message-id", (None,))[0] or f"{name}:{folder}:{uid}",
                    subject=msg.subject, sender=msg.from_, date=msg.date_str,
                    body_text=msg.text or msg.html or "",
                )
                if pk is None:
                    continue  # 중복
                stats["new_messages"] += 1

                for att in msg.attachments:
                    fmt = detect_format(att.filename)
                    dest = ATTACH_DIR / name / f"{uid}_{_safe_name(att.filename)}"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(att.payload)
                    stats["attachments"] += 1

                    extracted_text, status, draft = None, "pending", None
                    try:
                        draft = extract_file(str(dest), att.filename)
                        extracted_text, status = draft.body_text, "done"
                        stats["extracted"] += 1
                    except ExtractError:
                        status = "manual"   # 수동 처리 큐
                        stats["manual"] += 1

                    att_id = db.insert_attachment(
                        conn, message_pk=pk, filename=att.filename, format=fmt,
                        path=str(dest), size=len(att.payload),
                        extracted_text=extracted_text, extract_status=status,
                    )
                    if draft and draft.images:
                        images.process_images(conn, att_id, draft)

            db.set_last_uid(conn, name, folder, max_uid)
            conn.commit()

    conn.close()
    return stats


def collect_all(targets: list[str] | None = None) -> list[dict]:
    return [collect_account(n) for n in (targets or list(ACCOUNTS))]
