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

load_dotenv()

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


def get_last_uid(conn, account: str, folder: str) -> int:
    row = conn.execute(
        "SELECT last_uid FROM folder_state WHERE account=? AND folder=?",
        (account, folder)).fetchone()
    return row["last_uid"] if row else 0


def set_last_uid(conn, account: str, folder: str, uid: int) -> None:
    conn.execute(
        """INSERT INTO folder_state (account, folder, last_uid) VALUES (?,?,?)
           ON CONFLICT (account, folder) DO UPDATE SET last_uid=excluded.last_uid""",
        (account, folder, uid))


def insert_message(conn, **kw) -> int | None:
    """메일 1건 저장. message_id 중복이면 건너뜀(None 반환)."""
    row = conn.execute(
        """INSERT INTO messages (account, folder, uid, message_id, subject, sender, date, body_text)
           VALUES (:account,:folder,:uid,:message_id,:subject,:sender,:date,:body_text)
           ON CONFLICT (message_id) DO NOTHING
           RETURNING id""", kw).fetchone()
    return row["id"] if row else None


def messages_to_triage(conn, only_new: bool = True) -> list:
    q = "SELECT id, subject, sender, body_text FROM messages"
    if only_new:
        q += " WHERE pipeline IS NULL"
    return conn.execute(q + " ORDER BY id").fetchall()


def message_attachments(conn, message_pk: int) -> list[dict]:
    rows = conn.execute(
        "SELECT filename, format, size FROM attachments WHERE message_pk=? ORDER BY id",
        (message_pk,)).fetchall()
    return [dict(r) for r in rows]


def set_triage(conn, message_pk: int, pipeline: str,
               confidence: float | None, reason: str) -> None:
    conn.execute(
        "UPDATE messages SET pipeline=?, triage_confidence=?, triage_reason=? WHERE id=?",
        (pipeline, confidence, reason, message_pk))


def insert_attachment(conn, **kw) -> int:
    row = conn.execute(
        """INSERT INTO attachments (message_pk, filename, format, path, size, extracted_text, extract_status)
           VALUES (:message_pk,:filename,:format,:path,:size,:extracted_text,:extract_status)
           RETURNING id""", kw).fetchone()
    return row["id"]


# --- 기자 UI 용 조회/저장 (레거시 drafts) ---

def list_items(conn, status: str | None = None) -> list:
    where, params = "", []
    if status == "none":
        where = "WHERE d.status IS NULL"
    elif status in ("draft", "reviewed", "published"):
        where = "WHERE d.status = ?"
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


def status_counts(conn) -> dict:
    rows = conn.execute(
        """SELECT COALESCE(d.status,'none') s, COUNT(*) c
           FROM attachments a LEFT JOIN drafts d ON d.attachment_id=a.id
           GROUP BY COALESCE(d.status,'none')""").fetchall()
    counts = {r["s"]: r["c"] for r in rows}
    counts["all"] = sum(counts.values())
    return counts


def get_item(conn, att_id: int):
    return conn.execute(
        """SELECT a.id, a.filename, a.format, a.extract_status, a.extracted_text,
                  m.subject, m.sender, m.date,
                  d.headline, d.content, d.status AS draft_status,
                  d.published_url, d.published_at
           FROM attachments a
           JOIN messages m ON m.id = a.message_pk
           LEFT JOIN drafts d ON d.attachment_id = a.id
           WHERE a.id = ?""", (att_id,)).fetchone()


def upsert_draft(conn, att_id: int, headline: str, content: str, status: str = "draft") -> None:
    conn.execute(
        """INSERT INTO drafts (attachment_id, headline, content, status, updated_at)
           VALUES (?,?,?,?, datetime('now'))
           ON CONFLICT (attachment_id) DO UPDATE SET
               headline=excluded.headline, content=excluded.content,
               status=excluded.status, updated_at=now()""",
        (att_id, headline, content, status))


def set_draft_status(conn, att_id: int, status: str) -> None:
    conn.execute(
        "UPDATE drafts SET status=?, updated_at=datetime('now') WHERE attachment_id=?",
        (status, att_id))


# --- 기사(Split 결과 단위) ---

def clear_articles(conn, attachment_id: int) -> None:
    conn.execute("DELETE FROM articles WHERE attachment_id=?", (attachment_id,))


def insert_article(conn, **kw) -> int:
    row = conn.execute(
        """INSERT INTO articles (attachment_id, sequence_number, title, body,
                                 contact_info, category_hint, status)
           VALUES (:attachment_id,:sequence_number,:title,:body,
                   :contact_info,:category_hint,:status)
           RETURNING id""", kw).fetchone()
    return row["id"]


def list_articles(conn, attachment_id: int) -> list:
    return conn.execute(
        "SELECT * FROM articles WHERE attachment_id=? ORDER BY sequence_number, id",
        (attachment_id,)).fetchall()


def get_article(conn, article_id: int):
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def update_article_generated(conn, article_id: int, *, headline: str, subtitle: str,
                             content_html: str, category_code: str, article_type: str,
                             source_info: str, editor_notes: str) -> None:
    conn.execute(
        """UPDATE articles SET headline=?, subtitle=?, content_html=?, category_code=?,
               article_type=?, source_info=?, editor_notes=?, status='drafted' WHERE id=?""",
        (headline, subtitle, content_html, category_code, article_type,
         source_info, editor_notes, article_id))


def update_article_edit(conn, article_id: int, *, headline: str, subtitle: str,
                        content_html: str, category_code: str) -> None:
    conn.execute(
        "UPDATE articles SET headline=?, subtitle=?, content_html=?, category_code=? WHERE id=?",
        (headline, subtitle, content_html, category_code, article_id))


def article_status_counts(conn) -> dict:
    rows = conn.execute("SELECT status s, COUNT(*) c FROM articles GROUP BY status").fetchall()
    counts = {r["s"]: r["c"] for r in rows}
    counts["all"] = sum(counts.values())
    return counts


def set_article_status(conn, article_id: int, status: str) -> None:
    conn.execute("UPDATE articles SET status=? WHERE id=?", (status, article_id))


def mark_article_published(conn, article_id: int, url: str) -> None:
    conn.execute(
        "UPDATE articles SET status='published', published_url=?, published_at=now() WHERE id=?",
        (url, article_id))


def list_all_articles(conn, status: str | None = None) -> list:
    q = ("SELECT ar.*, m.subject AS email_subject, m.sender AS email_from "
         "FROM articles ar "
         "LEFT JOIN attachments a ON a.id=ar.attachment_id "
         "LEFT JOIN messages m ON m.id=a.message_pk")
    params = []
    if status and status != "all":
        q += " WHERE ar.status=?"
        params.append(status)
    return conn.execute(q + " ORDER BY ar.id DESC", params).fetchall()


def assign_image_article(conn, image_id: int, article_id: int | None) -> None:
    conn.execute("UPDATE images SET article_id=? WHERE id=?", (article_id, image_id))


def list_article_images(conn, article_id: int) -> list:
    return conn.execute(
        "SELECT * FROM images WHERE article_id=? ORDER BY ord, id", (article_id,)).fetchall()


# --- 이미지 ---

def clear_images(conn, att_id: int) -> None:
    conn.execute("DELETE FROM images WHERE attachment_id=?", (att_id,))


def insert_image(conn, **kw) -> int:
    row = conn.execute(
        """INSERT INTO images (attachment_id, path, ext, width, height, bytes, kind, selected, caption, ord)
           VALUES (:attachment_id,:path,:ext,:width,:height,:bytes,:kind,:selected,:caption,:ord)
           RETURNING id""", kw).fetchone()
    return row["id"]


def list_images(conn, att_id: int) -> list:
    return conn.execute(
        "SELECT * FROM images WHERE attachment_id=? ORDER BY ord, id", (att_id,)).fetchall()


def set_image_selected(conn, image_id: int, selected: bool) -> None:
    conn.execute("UPDATE images SET selected=? WHERE id=?", (1 if selected else 0, image_id))


def mark_published(conn, att_id: int, url: str) -> None:
    conn.execute(
        """UPDATE drafts SET status='published', published_url=?,
               published_at=now(), updated_at=now() WHERE attachment_id=?""",
        (url, att_id))


def bulk_set_status(conn, att_ids: list[int], status: str) -> int:
    if not att_ids:
        return 0
    placeholders = ",".join("?" * len(att_ids))
    cur = conn.execute(
        f"UPDATE drafts SET status=?, updated_at=now() WHERE attachment_id IN ({placeholders})",
        (status, *att_ids))
    return cur.rowcount
