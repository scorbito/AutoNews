"""SQLite 저장소 — 수집 메일/첨부/폴더 처리상태.

MVP는 SQLite, 운영 시 PostgreSQL 로 전환(스키마 동일하게 유지).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "aute_news.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folder_state (
    account   TEXT NOT NULL,
    folder    TEXT NOT NULL,
    last_uid  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account, folder)
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    folder      TEXT NOT NULL,
    uid         INTEGER NOT NULL,
    message_id  TEXT UNIQUE,
    subject     TEXT,
    sender      TEXT,
    date        TEXT,
    body_text   TEXT,
    status      TEXT NOT NULL DEFAULT 'collected',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_pk      INTEGER NOT NULL REFERENCES messages(id),
    filename        TEXT,
    format          TEXT,
    path            TEXT,
    size            INTEGER,
    extracted_text  TEXT,
    extract_status  TEXT NOT NULL DEFAULT 'pending',  -- pending/done/failed/manual
    FOREIGN KEY (message_pk) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS images (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id   INTEGER NOT NULL REFERENCES attachments(id),
    path            TEXT,
    ext             TEXT,
    width           INTEGER,
    height          INTEGER,
    bytes           INTEGER,
    kind            TEXT DEFAULT 'unknown',   -- photo/stamp/logo/diagram/unknown
    selected        INTEGER NOT NULL DEFAULT 0,
    caption         TEXT DEFAULT '',
    ord             INTEGER DEFAULT 0,
    article_id      INTEGER                   -- 매칭된 기사(이미지-기사 매칭 결과)
);

CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id   INTEGER REFERENCES attachments(id),
    sequence_number INTEGER DEFAULT 1,
    title           TEXT,
    body            TEXT,                              -- Split 결과 원문(분할만)
    contact_info    TEXT,                              -- JSON 문자열
    category_hint   TEXT,
    -- Generate 결과(이후 단계에서 채움)
    headline        TEXT,
    subtitle        TEXT,
    content_html    TEXT,
    category_code   TEXT,
    article_type    TEXT,
    source_info     TEXT,                              -- JSON
    editor_notes    TEXT,                              -- JSON
    status          TEXT NOT NULL DEFAULT 'split',     -- split/drafted/reviewed/published
    published_url   TEXT,
    published_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drafts (
    attachment_id   INTEGER PRIMARY KEY REFERENCES attachments(id),
    headline        TEXT,
    content         TEXT,                              -- 편집 가능한 본문(마크다운)
    status          TEXT NOT NULL DEFAULT 'draft',     -- draft/reviewed/published
    published_url   TEXT,                              -- 발행 결과(URL/파일경로)
    published_at    TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """기존 테이블에 누락 컬럼을 멱등적으로 추가."""
    dcols = {r["name"] for r in conn.execute("PRAGMA table_info(drafts)")}
    for name, decl in (("published_url", "TEXT"), ("published_at", "TEXT")):
        if name not in dcols:
            conn.execute(f"ALTER TABLE drafts ADD COLUMN {name} {decl}")
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    for name, decl in (("pipeline", "TEXT"), ("triage_confidence", "REAL"),
                       ("triage_reason", "TEXT")):
        if name not in mcols:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {decl}")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
    for name, decl in (("article_type", "TEXT"), ("source_info", "TEXT"),
                       ("editor_notes", "TEXT")):
        if name not in acols:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {name} {decl}")
    icols = {r["name"] for r in conn.execute("PRAGMA table_info(images)")}
    if "article_id" not in icols:
        conn.execute("ALTER TABLE images ADD COLUMN article_id INTEGER")
    conn.commit()


def get_last_uid(conn: sqlite3.Connection, account: str, folder: str) -> int:
    row = conn.execute(
        "SELECT last_uid FROM folder_state WHERE account=? AND folder=?",
        (account, folder),
    ).fetchone()
    return row["last_uid"] if row else 0


def set_last_uid(conn: sqlite3.Connection, account: str, folder: str, uid: int) -> None:
    conn.execute(
        """INSERT INTO folder_state (account, folder, last_uid) VALUES (?,?,?)
           ON CONFLICT(account, folder) DO UPDATE SET last_uid=excluded.last_uid""",
        (account, folder, uid),
    )


def insert_message(conn: sqlite3.Connection, **kw) -> int | None:
    """메일 1건 저장. message_id 중복이면 건너뜀(None 반환)."""
    try:
        cur = conn.execute(
            """INSERT INTO messages (account, folder, uid, message_id, subject, sender, date, body_text)
               VALUES (:account,:folder,:uid,:message_id,:subject,:sender,:date,:body_text)""",
            kw,
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # 이미 수집된 메일(message_id UNIQUE)


def messages_to_triage(conn: sqlite3.Connection, only_new: bool = True) -> list[sqlite3.Row]:
    q = "SELECT id, subject, sender, body_text FROM messages"
    if only_new:
        q += " WHERE pipeline IS NULL"
    return conn.execute(q + " ORDER BY id").fetchall()


def message_attachments(conn: sqlite3.Connection, message_pk: int) -> list[dict]:
    rows = conn.execute(
        "SELECT filename, format, size FROM attachments WHERE message_pk=? ORDER BY id",
        (message_pk,)).fetchall()
    return [dict(r) for r in rows]


def set_triage(conn: sqlite3.Connection, message_pk: int, pipeline: str,
               confidence: float | None, reason: str) -> None:
    conn.execute(
        "UPDATE messages SET pipeline=?, triage_confidence=?, triage_reason=? WHERE id=?",
        (pipeline, confidence, reason, message_pk))
    conn.commit()


def insert_attachment(conn: sqlite3.Connection, **kw) -> int:
    cur = conn.execute(
        """INSERT INTO attachments (message_pk, filename, format, path, size, extracted_text, extract_status)
           VALUES (:message_pk,:filename,:format,:path,:size,:extracted_text,:extract_status)""",
        kw,
    )
    return cur.lastrowid


# --- 기자 UI 용 조회/저장 ---

def list_items(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    """수집 목록: 첨부 + 메일 + 초안 상태. status 로 초안상태 필터."""
    where, params = "", []
    if status == "none":          # 초안 미생성
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
            ORDER BY a.id DESC""",
        params,
    ).fetchall()


def status_counts(conn: sqlite3.Connection) -> dict:
    """상태별 건수(필터 탭 표시용)."""
    rows = conn.execute(
        """SELECT COALESCE(d.status,'none') s, COUNT(*) c
           FROM attachments a LEFT JOIN drafts d ON d.attachment_id=a.id
           GROUP BY COALESCE(d.status,'none')"""
    ).fetchall()
    counts = {r["s"]: r["c"] for r in rows}
    counts["all"] = sum(counts.values())
    return counts


def get_item(conn: sqlite3.Connection, att_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT a.id, a.filename, a.format, a.extract_status, a.extracted_text,
                  m.subject, m.sender, m.date,
                  d.headline, d.content, d.status AS draft_status,
                  d.published_url, d.published_at
           FROM attachments a
           JOIN messages m ON m.id = a.message_pk
           LEFT JOIN drafts d ON d.attachment_id = a.id
           WHERE a.id = ?""",
        (att_id,),
    ).fetchone()


def upsert_draft(conn: sqlite3.Connection, att_id: int, headline: str,
                 content: str, status: str = "draft") -> None:
    conn.execute(
        """INSERT INTO drafts (attachment_id, headline, content, status, updated_at)
           VALUES (?,?,?,?, datetime('now'))
           ON CONFLICT(attachment_id) DO UPDATE SET
               headline=excluded.headline, content=excluded.content,
               status=excluded.status, updated_at=datetime('now')""",
        (att_id, headline, content, status),
    )
    conn.commit()


def set_draft_status(conn: sqlite3.Connection, att_id: int, status: str) -> None:
    conn.execute(
        "UPDATE drafts SET status=?, updated_at=datetime('now') WHERE attachment_id=?",
        (status, att_id),
    )
    conn.commit()


# --- 기사(Split 결과 단위) ---

def clear_articles(conn: sqlite3.Connection, attachment_id: int) -> None:
    conn.execute("DELETE FROM articles WHERE attachment_id=?", (attachment_id,))


def insert_article(conn: sqlite3.Connection, **kw) -> int:
    cur = conn.execute(
        """INSERT INTO articles (attachment_id, sequence_number, title, body,
                                 contact_info, category_hint, status)
           VALUES (:attachment_id,:sequence_number,:title,:body,
                   :contact_info,:category_hint,:status)""", kw)
    return cur.lastrowid


def list_articles(conn: sqlite3.Connection, attachment_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM articles WHERE attachment_id=? ORDER BY sequence_number, id",
        (attachment_id,)).fetchall()


def get_article(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def update_article_generated(conn: sqlite3.Connection, article_id: int, *, headline: str,
                             subtitle: str, content_html: str, category_code: str,
                             article_type: str, source_info: str, editor_notes: str) -> None:
    conn.execute(
        """UPDATE articles SET headline=?, subtitle=?, content_html=?, category_code=?,
               article_type=?, source_info=?, editor_notes=?, status='drafted' WHERE id=?""",
        (headline, subtitle, content_html, category_code, article_type,
         source_info, editor_notes, article_id))
    conn.commit()


def update_article_edit(conn: sqlite3.Connection, article_id: int, *, headline: str,
                        subtitle: str, content_html: str, category_code: str) -> None:
    conn.execute(
        """UPDATE articles SET headline=?, subtitle=?, content_html=?, category_code=?
           WHERE id=?""", (headline, subtitle, content_html, category_code, article_id))
    conn.commit()


def article_status_counts(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT status s, COUNT(*) c FROM articles GROUP BY status").fetchall()
    counts = {r["s"]: r["c"] for r in rows}
    counts["all"] = sum(counts.values())
    return counts


def set_article_status(conn: sqlite3.Connection, article_id: int, status: str) -> None:
    conn.execute("UPDATE articles SET status=? WHERE id=?", (status, article_id))
    conn.commit()


def mark_article_published(conn: sqlite3.Connection, article_id: int, url: str) -> None:
    conn.execute(
        """UPDATE articles SET status='published', published_url=?,
               published_at=datetime('now') WHERE id=?""", (url, article_id))
    conn.commit()


def list_all_articles(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    q = ("SELECT ar.*, m.subject AS email_subject, m.sender AS email_from "
         "FROM articles ar "
         "LEFT JOIN attachments a ON a.id=ar.attachment_id "
         "LEFT JOIN messages m ON m.id=a.message_pk")
    params = []
    if status and status != "all":
        q += " WHERE ar.status=?"
        params.append(status)
    return conn.execute(q + " ORDER BY ar.id DESC", params).fetchall()


def assign_image_article(conn: sqlite3.Connection, image_id: int, article_id: int | None) -> None:
    conn.execute("UPDATE images SET article_id=? WHERE id=?", (article_id, image_id))
    conn.commit()


def list_article_images(conn: sqlite3.Connection, article_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM images WHERE article_id=? ORDER BY ord, id", (article_id,)).fetchall()


# --- 이미지 ---

def clear_images(conn: sqlite3.Connection, att_id: int) -> None:
    conn.execute("DELETE FROM images WHERE attachment_id=?", (att_id,))


def insert_image(conn: sqlite3.Connection, **kw) -> int:
    cur = conn.execute(
        """INSERT INTO images (attachment_id, path, ext, width, height, bytes, kind, selected, caption, ord)
           VALUES (:attachment_id,:path,:ext,:width,:height,:bytes,:kind,:selected,:caption,:ord)""",
        kw,
    )
    return cur.lastrowid


def list_images(conn: sqlite3.Connection, att_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM images WHERE attachment_id=? ORDER BY ord, id", (att_id,)
    ).fetchall()


def set_image_selected(conn: sqlite3.Connection, image_id: int, selected: bool) -> None:
    conn.execute("UPDATE images SET selected=? WHERE id=?", (1 if selected else 0, image_id))
    conn.commit()


def mark_published(conn: sqlite3.Connection, att_id: int, url: str) -> None:
    conn.execute(
        """UPDATE drafts SET status='published', published_url=?,
                  published_at=datetime('now'), updated_at=datetime('now')
           WHERE attachment_id=?""",
        (url, att_id),
    )
    conn.commit()


def bulk_set_status(conn: sqlite3.Connection, att_ids: list[int], status: str) -> int:
    """선택된 첨부들의 초안 상태를 일괄 변경(초안이 있는 것만 반영). 반영 건수 반환."""
    if not att_ids:
        return 0
    placeholders = ",".join("?" * len(att_ids))
    cur = conn.execute(
        f"UPDATE drafts SET status=?, updated_at=datetime('now') "
        f"WHERE attachment_id IN ({placeholders})",
        (status, *att_ids),
    )
    conn.commit()
    return cur.rowcount
