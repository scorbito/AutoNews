-- auto_news 멀티테넌트 Postgres 스키마 (Supabase)
-- 모든 데이터 테이블에 tenant_id (신문사별 격리).

-- 신문사(테넌트)
CREATE TABLE IF NOT EXISTS tenants (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 기본 테넌트(개발/전환용, id=1)
INSERT INTO tenants (id, name, slug) VALUES (1, '기본', 'default')
    ON CONFLICT (id) DO NOTHING;

-- 사용자 ↔ 테넌트 매핑 (Supabase Auth user.id ↔ 신문사)
CREATE TABLE IF NOT EXISTS tenant_users (
    user_id    UUID PRIMARY KEY,        -- Supabase auth.users.id
    tenant_id  BIGINT NOT NULL REFERENCES tenants(id),
    email      TEXT,
    role       TEXT NOT NULL DEFAULT 'editor',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant ON tenant_users(tenant_id);

-- 테넌트별 설정 (메일/CMS, 비밀번호는 Fernet 암호화 저장)
CREATE TABLE IF NOT EXISTS tenant_config (
    tenant_id          BIGINT PRIMARY KEY REFERENCES tenants(id),
    imap_host          TEXT,
    imap_email         TEXT,
    imap_password_enc  TEXT,
    imap_folders       TEXT,
    publisher          TEXT NOT NULL DEFAULT 'html',
    ndsoft_base_url    TEXT,
    cms_user           TEXT,
    cms_password_enc   TEXT,
    cms_user_name      TEXT,
    cms_user_email     TEXT,
    cms_section        TEXT DEFAULT 'S1N10',
    pipeline_mode      TEXT NOT NULL DEFAULT 'review',
    collect_enabled    INTEGER NOT NULL DEFAULT 0,
    collect_times      TEXT,                          -- "09:00,15:00" (KST)
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 기자(사용자)별 메일 설정 (메일은 신문사 공용이 아니라 기자 개인 계정)
CREATE TABLE IF NOT EXISTS user_mail_config (
    user_id           UUID PRIMARY KEY REFERENCES tenant_users(user_id),
    tenant_id         BIGINT NOT NULL REFERENCES tenants(id),
    imap_host         TEXT,
    imap_email        TEXT,
    imap_password_enc TEXT,
    imap_folders      TEXT,
    collect_enabled   INTEGER NOT NULL DEFAULT 0,
    collect_all       INTEGER NOT NULL DEFAULT 0,   -- 테스트: 기간 무시 전체 수집
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_mail_tenant ON user_mail_config(tenant_id);

-- 기존 데이터 테이블 재생성(멀티테넌트). 빈 상태에서만 안전.
DROP TABLE IF EXISTS drafts, images, articles, attachments, messages, folder_state CASCADE;

CREATE TABLE folder_state (
    tenant_id BIGINT NOT NULL REFERENCES tenants(id),
    account   TEXT NOT NULL,
    folder    TEXT NOT NULL,
    last_uid  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, account, folder)
);

CREATE TABLE messages (
    id                 BIGSERIAL PRIMARY KEY,
    tenant_id          BIGINT NOT NULL REFERENCES tenants(id),
    account            TEXT NOT NULL,
    folder             TEXT NOT NULL,
    uid                BIGINT NOT NULL,
    message_id         TEXT,
    subject            TEXT,
    sender             TEXT,
    date               TEXT,
    body_text          TEXT,
    status             TEXT NOT NULL DEFAULT 'collected',
    pipeline           TEXT,
    triage_confidence  REAL,
    triage_reason      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, message_id)
);

CREATE TABLE attachments (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       BIGINT NOT NULL REFERENCES tenants(id),
    message_pk      BIGINT NOT NULL REFERENCES messages(id),
    filename        TEXT,
    format          TEXT,
    path            TEXT,
    size            BIGINT,
    extracted_text  TEXT,
    extract_status  TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE articles (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       BIGINT NOT NULL REFERENCES tenants(id),
    attachment_id   BIGINT REFERENCES attachments(id),
    sequence_number INTEGER DEFAULT 1,
    title           TEXT,
    body            TEXT,
    contact_info    TEXT,
    category_hint   TEXT,
    headline        TEXT,
    subtitle        TEXT,
    content_html    TEXT,
    category_code   TEXT,
    article_type    TEXT,
    source_info     TEXT,
    editor_notes    TEXT,
    status          TEXT NOT NULL DEFAULT 'split',
    published_url   TEXT,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE images (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       BIGINT NOT NULL REFERENCES tenants(id),
    attachment_id   BIGINT NOT NULL REFERENCES attachments(id),
    path            TEXT,
    orig_name       TEXT,                       -- 원본 파일명(ZIP 등) — LLM 매칭/번호 힌트용
    source          TEXT,                       -- 출처(웹 링크에서 온 사진의 매체/URL)
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

CREATE TABLE drafts (
    attachment_id   BIGINT PRIMARY KEY REFERENCES attachments(id),
    tenant_id       BIGINT NOT NULL REFERENCES tenants(id),
    headline        TEXT,
    content         TEXT,
    status          TEXT NOT NULL DEFAULT 'draft',
    published_url   TEXT,
    published_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 테넌트/조회 인덱스
CREATE INDEX idx_messages_tenant    ON messages(tenant_id);
CREATE INDEX idx_attachments_tenant ON attachments(tenant_id);
CREATE INDEX idx_attachments_msg    ON attachments(message_pk);
CREATE INDEX idx_articles_tenant    ON articles(tenant_id);
CREATE INDEX idx_articles_att       ON articles(attachment_id);
CREATE INDEX idx_images_tenant      ON images(tenant_id);
CREATE INDEX idx_images_att         ON images(attachment_id);
CREATE INDEX idx_images_article     ON images(article_id);
