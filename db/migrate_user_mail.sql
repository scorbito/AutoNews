-- 기자(사용자)별 메일 설정 — 메일을 신문사(tenant) 단위에서 기자(user) 단위로.
-- Supabase SQL 편집기에서 1회 실행. 기존 tenant_config 의 메일 컬럼은 그대로 두되
-- 이제 수집은 user_mail_config 를 우선 사용한다(없으면 tenant_config 폴백).

CREATE TABLE IF NOT EXISTS user_mail_config (
    user_id           UUID PRIMARY KEY REFERENCES tenant_users(user_id),
    tenant_id         BIGINT NOT NULL REFERENCES tenants(id),
    imap_host         TEXT,
    imap_email        TEXT,
    imap_password_enc TEXT,
    imap_folders      TEXT,                          -- "내게쓴메일함,받은메일함"
    collect_enabled   INTEGER NOT NULL DEFAULT 0,    -- 예약 수집 대상 여부
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_mail_tenant ON user_mail_config(tenant_id);
