"""저장소 — Supabase PostgreSQL (psycopg).

기존 SQLite 코드의 함수 시그니처를 그대로 유지하기 위해, 얇은 연결 래퍼(_Conn)가
SQL 을 자동 변환한다: '?'→'%s', ':name'→'%(name)s', datetime('now')→now().
INSERT 의 lastrowid 는 RETURNING id 로 대체.
스키마는 Supabase 에 이미 생성됨(db/schema_postgres.sql).
"""
from __future__ import annotations

import os
import re

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from . import crypto

load_dotenv()

# 인증(2단계) 전까지의 기본 테넌트. 인증 후엔 호출부가 실제 tenant_id 를 넘긴다.
DEFAULT_TENANT = 1

_NAMED = re.compile(r":(\w+)")


def _translate(sql: str) -> str:
    sql = sql.replace("datetime('now')", "now()")
    sql = _NAMED.sub(r"%(\1)s", sql)   # :name  → %(name)s
    sql = sql.replace("?", "%s")        # ?      → %s
    return sql


class _Conn:
    """sqlite3.Connection 과 유사한 표면(execute/commit/close)을 제공하는 래퍼."""

    def __init__(self, raw: psycopg.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params=None):
        cur = self._raw.cursor()
        cur.execute(_translate(sql), params if params is not None else ())
        return cur

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()

    def cursor(self):
        return self._raw.cursor()


def connect() -> _Conn:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 이 .env 에 없습니다 (Supabase 연결 문자열).")
    raw = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=15)
    return _Conn(raw)


# --- 테넌트 설정(메일/CMS, 비밀번호 암호화) ---

_CFG_COLS = ("imap_host", "imap_email", "imap_folders", "publisher", "ndsoft_base_url",
             "cms_user", "cms_user_name", "cms_user_email", "cms_section", "pipeline_mode",
             "collect_enabled", "collect_times")


def get_tenant_config(conn, tenant_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tenant_config WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["imap_password"] = crypto.decrypt(d.pop("imap_password_enc", None))
    d["cms_password"] = crypto.decrypt(d.pop("cms_password_enc", None))
    return d


def set_tenant_config(conn, tenant_id: int, *, imap_password: str | None = None,
                      cms_password: str | None = None, **fields) -> None:
    """테넌트 설정 upsert. 제공된 필드만 갱신(None은 기존값 유지). 비번은 암호화."""
    cols = {k: v for k, v in fields.items() if k in _CFG_COLS}
    if imap_password is not None:
        cols["imap_password_enc"] = crypto.encrypt(imap_password)
    if cms_password is not None:
        cols["cms_password_enc"] = crypto.encrypt(cms_password)
    names = ["tenant_id", *cols.keys()]
    placeholders = ",".join(["?"] * len(names))
    updates = ", ".join(f"{c}=COALESCE(excluded.{c}, tenant_config.{c})" for c in cols)
    updates = (updates + ", " if updates else "") + "updated_at=now()"
    conn.execute(
        f"INSERT INTO tenant_config ({','.join(names)}) VALUES ({placeholders}) "
        f"ON CONFLICT (tenant_id) DO UPDATE SET {updates}",
        (tenant_id, *cols.values()))


# --- 기자(사용자)별 메일 설정 ---

_USER_MAIL_COLS = ("imap_host", "imap_email", "imap_folders", "collect_enabled", "collect_all")


def get_user_mail(conn, user_id: str) -> dict | None:
    """기자 메일 설정(비번 복호화 포함). 없으면 None."""
    row = conn.execute("SELECT * FROM user_mail_config WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["imap_password"] = crypto.decrypt(d.pop("imap_password_enc", None))
    return d


def set_user_mail(conn, user_id: str, tenant_id: int, *, imap_password: str | None = None,
                  **fields) -> None:
    """기자 메일 설정 upsert. 제공된 필드만 갱신(None은 기존값 유지). 비번은 암호화."""
    cols = {k: v for k, v in fields.items() if k in _USER_MAIL_COLS}
    if imap_password is not None:
        cols["imap_password_enc"] = crypto.encrypt(imap_password)
    names = ["user_id", "tenant_id", *cols.keys()]
    placeholders = ",".join(["?"] * len(names))
    updates = ", ".join(f"{c}=COALESCE(excluded.{c}, user_mail_config.{c})" for c in cols)
    updates = (updates + ", " if updates else "") + "updated_at=now()"
    conn.execute(
        f"INSERT INTO user_mail_config ({','.join(names)}) VALUES ({placeholders}) "
        f"ON CONFLICT (user_id) DO UPDATE SET {updates}",
        (user_id, tenant_id, *cols.values()))


def list_tenant_mail_accounts(conn, tenant_id: int, only_enabled: bool = False) -> list[dict]:
    """신문사 소속 기자들의 메일 설정 목록(비번 복호화 포함)."""
    q = "SELECT * FROM user_mail_config WHERE tenant_id=?"
    if only_enabled:
        q += " AND collect_enabled=1"
    rows = conn.execute(q, (tenant_id,)).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["imap_password"] = crypto.decrypt(d.pop("imap_password_enc", None))
        out.append(d)
    return out


def folder_initialized(conn, account: str, folder: str,
                       tenant_id: int = DEFAULT_TENANT) -> bool:
    """이 폴더의 수집 기준선(folder_state)이 이미 있는지. 최초 수집 판별용."""
    row = conn.execute(
        "SELECT 1 FROM folder_state WHERE tenant_id=? AND account=? AND folder=?",
        (tenant_id, account, folder)).fetchone()
    return row is not None


def get_last_uid(conn, account: str, folder: str, tenant_id: int = DEFAULT_TENANT) -> int:
    row = conn.execute(
        "SELECT last_uid FROM folder_state WHERE tenant_id=? AND account=? AND folder=?",
        (tenant_id, account, folder)).fetchone()
    return row["last_uid"] if row else 0


def set_last_uid(conn, account: str, folder: str, uid: int,
                 tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        """INSERT INTO folder_state (tenant_id, account, folder, last_uid) VALUES (?,?,?,?)
           ON CONFLICT (tenant_id, account, folder) DO UPDATE SET last_uid=excluded.last_uid""",
        (tenant_id, account, folder, uid))


def insert_message(conn, **kw) -> int | None:
    """메일 1건 저장. (tenant_id, message_id) 중복이면 건너뜀(None). kw 에 tenant_id 필요."""
    kw.setdefault("tenant_id", DEFAULT_TENANT)
    row = conn.execute(
        """INSERT INTO messages (tenant_id, account, folder, uid, message_id, subject, sender, date, body_text)
           VALUES (:tenant_id,:account,:folder,:uid,:message_id,:subject,:sender,:date,:body_text)
           ON CONFLICT (tenant_id, message_id) DO NOTHING
           RETURNING id""", kw).fetchone()
    return row["id"] if row else None


def messages_to_triage(conn, only_new: bool = True, tenant_id: int = DEFAULT_TENANT) -> list:
    q = "SELECT id, subject, sender, body_text FROM messages WHERE tenant_id=?"
    if only_new:
        q += " AND pipeline IS NULL"
    return conn.execute(q + " ORDER BY id", (tenant_id,)).fetchall()


def message_attachments(conn, message_pk: int, tenant_id: int = DEFAULT_TENANT) -> list[dict]:
    rows = conn.execute(
        "SELECT filename, format, size FROM attachments WHERE message_pk=? AND tenant_id=? ORDER BY id",
        (message_pk, tenant_id)).fetchall()
    return [dict(r) for r in rows]


def set_triage(conn, message_pk: int, pipeline: str, confidence: float | None,
               reason: str, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        "UPDATE messages SET pipeline=?, triage_confidence=?, triage_reason=? WHERE id=? AND tenant_id=?",
        (pipeline, confidence, reason, message_pk, tenant_id))


def insert_attachment(conn, **kw) -> int:
    kw.setdefault("tenant_id", DEFAULT_TENANT)
    row = conn.execute(
        """INSERT INTO attachments (tenant_id, message_pk, filename, format, path, size, extracted_text, extract_status)
           VALUES (:tenant_id,:message_pk,:filename,:format,:path,:size,:extracted_text,:extract_status)
           RETURNING id""", kw).fetchone()
    return row["id"]


# --- 기자 UI 용 조회/저장 (레거시 drafts) ---

def list_items(conn, status: str | None = None, tenant_id: int = DEFAULT_TENANT) -> list:
    where, params = "WHERE a.tenant_id=?", [tenant_id]
    if status == "none":
        where += " AND d.status IS NULL"
    elif status in ("draft", "reviewed", "published"):
        where += " AND d.status = ?"
        params.append(status)
    return conn.execute(
        f"""SELECT a.id, a.filename, a.format, a.extract_status,
                   m.subject, m.sender, m.date,
                   d.status AS draft_status, d.headline
            FROM attachments a
            JOIN messages m ON m.id = a.message_pk
            LEFT JOIN drafts d ON d.attachment_id = a.id
            {where}
            ORDER BY a.id DESC""", params).fetchall()


def status_counts(conn, tenant_id: int = DEFAULT_TENANT) -> dict:
    rows = conn.execute(
        """SELECT COALESCE(d.status,'none') s, COUNT(*) c
           FROM attachments a LEFT JOIN drafts d ON d.attachment_id=a.id
           WHERE a.tenant_id=?
           GROUP BY COALESCE(d.status,'none')""", (tenant_id,)).fetchall()
    counts = {r["s"]: r["c"] for r in rows}
    counts["all"] = sum(counts.values())
    return counts


def get_item(conn, att_id: int, tenant_id: int = DEFAULT_TENANT):
    return conn.execute(
        """SELECT a.id, a.filename, a.format, a.extract_status, a.extracted_text,
                  m.subject, m.sender, m.date,
                  d.headline, d.content, d.status AS draft_status,
                  d.published_url, d.published_at
           FROM attachments a
           JOIN messages m ON m.id = a.message_pk
           LEFT JOIN drafts d ON d.attachment_id = a.id
           WHERE a.id = ? AND a.tenant_id = ?""", (att_id, tenant_id)).fetchone()


def upsert_draft(conn, att_id: int, headline: str, content: str, status: str = "draft",
                 tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        """INSERT INTO drafts (attachment_id, tenant_id, headline, content, status, updated_at)
           VALUES (?,?,?,?,?, now())
           ON CONFLICT (attachment_id) DO UPDATE SET
               headline=excluded.headline, content=excluded.content,
               status=excluded.status, updated_at=now()""",
        (att_id, tenant_id, headline, content, status))


def set_draft_status(conn, att_id: int, status: str, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        "UPDATE drafts SET status=?, updated_at=now() WHERE attachment_id=? AND tenant_id=?",
        (status, att_id, tenant_id))


# --- 기사(Split 결과 단위) ---

def clear_articles(conn, attachment_id: int, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute("DELETE FROM articles WHERE attachment_id=? AND tenant_id=?",
                 (attachment_id, tenant_id))


def insert_article(conn, **kw) -> int:
    kw.setdefault("tenant_id", DEFAULT_TENANT)
    row = conn.execute(
        """INSERT INTO articles (tenant_id, attachment_id, sequence_number, title, body,
                                 contact_info, category_hint, status)
           VALUES (:tenant_id,:attachment_id,:sequence_number,:title,:body,
                   :contact_info,:category_hint,:status)
           RETURNING id""", kw).fetchone()
    return row["id"]


def list_articles(conn, attachment_id: int, tenant_id: int = DEFAULT_TENANT) -> list:
    return conn.execute(
        "SELECT * FROM articles WHERE attachment_id=? AND tenant_id=? ORDER BY sequence_number, id",
        (attachment_id, tenant_id)).fetchall()


def list_messages(conn, tenant_id: int = DEFAULT_TENANT, limit: int = 100) -> list:
    """수집된 메일 목록 + 요약(첨부 종류·이미지·기사 수·트리아지)."""
    return conn.execute(
        """SELECT m.id, m.subject, m.sender, m.date, m.pipeline, m.account, m.folder,
                  (SELECT COUNT(*) FROM attachments a WHERE a.message_pk=m.id) att_count,
                  (SELECT string_agg(DISTINCT a.format, ',') FROM attachments a
                     WHERE a.message_pk=m.id) att_formats,
                  (SELECT COUNT(*) FROM images i JOIN attachments a ON a.id=i.attachment_id
                     WHERE a.message_pk=m.id AND i.selected=1) img_count,
                  (SELECT COUNT(*) FROM articles ar JOIN attachments a ON a.id=ar.attachment_id
                     WHERE a.message_pk=m.id) art_count
           FROM messages m WHERE m.tenant_id=? ORDER BY m.id DESC LIMIT ?""",
        (tenant_id, limit)).fetchall()


def clear_synthetic_attachments(conn, message_pk: int, tenant_id: int = DEFAULT_TENANT) -> None:
    """재처리용 — weblink/body 합성 첨부와 그에 딸린 기사·이미지 제거(중복 누적 방지)."""
    sub = ("SELECT id FROM attachments WHERE message_pk=? AND tenant_id=? "
           "AND format IN ('weblink','body')")
    conn.execute(f"DELETE FROM images WHERE tenant_id=? AND attachment_id IN ({sub})",
                 (tenant_id, message_pk, tenant_id))
    conn.execute(f"DELETE FROM articles WHERE tenant_id=? AND attachment_id IN ({sub})",
                 (tenant_id, message_pk, tenant_id))
    conn.execute("DELETE FROM attachments WHERE message_pk=? AND tenant_id=? "
                 "AND format IN ('weblink','body')", (message_pk, tenant_id))


def list_message_images(conn, message_pk: int, tenant_id: int = DEFAULT_TENANT) -> list:
    """한 메일의 첨부 이미지 — zip 번들 + 문서 임베드 통합.

    weblink/body 첨부 이미지는 제외(그건 각 링크 기사에 첨부 단위로 따로 매칭됨).
    """
    return conn.execute(
        """SELECT i.* FROM images i JOIN attachments a ON a.id=i.attachment_id
           WHERE a.message_pk=? AND i.tenant_id=? AND a.format NOT IN ('weblink','body')
           ORDER BY i.id""",
        (message_pk, tenant_id)).fetchall()


def get_article(conn, article_id: int, tenant_id: int = DEFAULT_TENANT):
    return conn.execute("SELECT * FROM articles WHERE id=? AND tenant_id=?",
                        (article_id, tenant_id)).fetchone()


def update_article_generated(conn, article_id: int, *, headline: str, subtitle: str,
                             content_html: str, category_code: str, article_type: str,
                             source_info: str, editor_notes: str,
                             tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        """UPDATE articles SET headline=?, subtitle=?, content_html=?, category_code=?,
               article_type=?, source_info=?, editor_notes=?, status='drafted'
           WHERE id=? AND tenant_id=?""",
        (headline, subtitle, content_html, category_code, article_type,
         source_info, editor_notes, article_id, tenant_id))


def update_article_edit(conn, article_id: int, *, headline: str, subtitle: str,
                        content_html: str, category_code: str,
                        tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        "UPDATE articles SET headline=?, subtitle=?, content_html=?, category_code=? WHERE id=? AND tenant_id=?",
        (headline, subtitle, content_html, category_code, article_id, tenant_id))


def article_status_counts(conn, tenant_id: int = DEFAULT_TENANT) -> dict:
    rows = conn.execute(
        "SELECT status s, COUNT(*) c FROM articles WHERE tenant_id=? GROUP BY status",
        (tenant_id,)).fetchall()
    counts = {r["s"]: r["c"] for r in rows}
    counts["all"] = sum(counts.values())
    return counts


def set_article_status(conn, article_id: int, status: str, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute("UPDATE articles SET status=? WHERE id=? AND tenant_id=?",
                 (status, article_id, tenant_id))


def mark_article_published(conn, article_id: int, url: str, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        "UPDATE articles SET status='published', published_url=?, published_at=now() WHERE id=? AND tenant_id=?",
        (url, article_id, tenant_id))


def list_all_articles(conn, status: str | None = None, tenant_id: int = DEFAULT_TENANT) -> list:
    q = ("SELECT ar.*, m.id AS email_id, m.subject AS email_subject, "
         "m.sender AS email_from, m.date AS email_date, "
         "(SELECT COUNT(*) FROM images i WHERE i.article_id=ar.id AND i.tenant_id=ar.tenant_id "
         " AND i.selected=1) AS photo_count "
         "FROM articles ar "
         "LEFT JOIN attachments a ON a.id=ar.attachment_id "
         "LEFT JOIN messages m ON m.id=a.message_pk "
         "WHERE ar.tenant_id=?")
    params = [tenant_id]
    if status and status != "all":
        q += " AND ar.status=?"
        params.append(status)
    # 메일별로 묶이도록 메일 우선 정렬, 메일 안에서는 기사 순번
    return conn.execute(
        q + " ORDER BY m.id DESC NULLS LAST, ar.sequence_number, ar.id", params).fetchall()


def assign_image_article(conn, image_id: int, article_id: int | None,
                         tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute("UPDATE images SET article_id=? WHERE id=? AND tenant_id=?",
                 (article_id, image_id, tenant_id))


def list_article_images(conn, article_id: int, tenant_id: int = DEFAULT_TENANT) -> list:
    return conn.execute(
        "SELECT * FROM images WHERE article_id=? AND tenant_id=? ORDER BY ord, id",
        (article_id, tenant_id)).fetchall()


# --- 이미지 ---

def clear_images(conn, att_id: int, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute("DELETE FROM images WHERE attachment_id=? AND tenant_id=?", (att_id, tenant_id))


def insert_image(conn, **kw) -> int:
    kw.setdefault("tenant_id", DEFAULT_TENANT)
    kw.setdefault("orig_name", None)
    kw.setdefault("source", None)
    row = conn.execute(
        """INSERT INTO images (tenant_id, attachment_id, path, orig_name, source, ext, width, height, bytes, kind, selected, caption, ord)
           VALUES (:tenant_id,:attachment_id,:path,:orig_name,:source,:ext,:width,:height,:bytes,:kind,:selected,:caption,:ord)
           RETURNING id""", kw).fetchone()
    return row["id"]


def list_images(conn, att_id: int, tenant_id: int = DEFAULT_TENANT) -> list:
    return conn.execute(
        "SELECT * FROM images WHERE attachment_id=? AND tenant_id=? ORDER BY ord, id",
        (att_id, tenant_id)).fetchall()


def set_image_selected(conn, image_id: int, selected: bool, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute("UPDATE images SET selected=? WHERE id=? AND tenant_id=?",
                 (1 if selected else 0, image_id, tenant_id))


def mark_published(conn, att_id: int, url: str, tenant_id: int = DEFAULT_TENANT) -> None:
    conn.execute(
        """UPDATE drafts SET status='published', published_url=?,
               published_at=now(), updated_at=now() WHERE attachment_id=? AND tenant_id=?""",
        (url, att_id, tenant_id))


def bulk_set_status(conn, att_ids: list[int], status: str, tenant_id: int = DEFAULT_TENANT) -> int:
    if not att_ids:
        return 0
    placeholders = ",".join("?" * len(att_ids))
    cur = conn.execute(
        f"UPDATE drafts SET status=?, updated_at=now() "
        f"WHERE tenant_id=? AND attachment_id IN ({placeholders})",
        (status, tenant_id, *att_ids))
    return cur.rowcount
