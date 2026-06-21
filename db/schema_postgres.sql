-- auto_news Postgres 스키마 (Supabase SQL Editor 에 붙여넣고 Run)
-- SQLite(MVP) 스키마를 Postgres 로 이식. 멀티테넌시(tenant_id)는 이후 단계에서 추가.

CREATE TABLE IF NOT EXISTS folder_state (
    account   TEXT NOT NULL,
    folder    TEXT NOT NULL,
    last_uid  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (account, folder)
);

CREATE TABLE IF NOT EXISTS messages (
    id                 BIGSERIAL PRIMARY KEY,
    account            TEXT NOT NULL,
    folder             TEXT NOT NULL,
    uid                BIGINT NOT NULL,
    message_id         TEXT UNIQUE,
    subject            TEXT,
    sender             TEXT,
    date               TEXT,
    body_text          TEXT,
    status             TEXT NOT NULL DEFAULT 'collected',
    pipeline           TEXT,
    triage_confidence  REAL,
    triage_reason      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attachments (
    id              BIGSERIAL PRIMARY KEY,
    message_pk      BIGINT NOT NULL REFERENCES messages(id),
    filename        TEXT,
    format          TEXT,
    path            TEXT,
    size            BIGINT,
    extracted_text  TEXT,
    extract_status  TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS articles (
    id              BIGSERIAL PRIMARY KEY,
    attachment_id   BIGINT REFERENCES attachments(id),
    sequence_number INTEGER DEFAULT 1,
    title           TEXT,
    body            TEXT,
    contact_info    TEXT,          -- JSON 문자열
    category_hint   TEXT,
    headline        TEXT,
    subtitle        TEXT,
    content_html    TEXT,
    category_code   TEXT,
    article_type    TEXT,
    source_info     TEXT,          -- JSON
    editor_notes    TEXT,          -- JSON
    status          TEXT NOT NULL DEFAULT 'split',
    published_url   TEXT,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drafts (
    attachment_id   BIGINT PRIMARY KEY REFERENCES attachments(id),
    headline        TEXT,
    content         TEXT,
    status          TEXT NOT NULL DEFAULT 'draft',
    published_url   TEXT,
    published_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS images (
    id              BIGSERIAL PRIMARY KEY,
    attachment_id   BIGINT NOT NULL REFERENCES attachments(id),
    path            TEXT,
    ext             TEXT,
    width           INTEGER,
    height          INTEGER,
    bytes           BIGINT,
    kind            TEXT DEFAULT 'unknown',
    selected        INTEGER NOT NULL DEFAULT 0,
    caption         TEXT DEFAULT '',
    ord             INTEGER DEFAULT 0,
    article_id      BIGINT
);

-- 조회 성능용 인덱스
CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_pk);
CREATE INDEX IF NOT EXISTS idx_articles_att    ON articles(attachment_id);
CREATE INDEX IF NOT EXISTS idx_images_att      ON images(attachment_id);
CREATE INDEX IF NOT EXISTS idx_images_article  ON images(article_id);
