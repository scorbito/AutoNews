-- 문의·건의 게시판 (B방식: 같은 게시판, 기자는 본인 글만 / 관리자는 전체)
CREATE TABLE IF NOT EXISTS inquiries (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   BIGINT NOT NULL REFERENCES tenants(id),
    user_id     UUID,
    user_email  TEXT,
    kind        TEXT NOT NULL DEFAULT '문의',     -- 문의 | 건의
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',      -- open | answered | closed
    reply       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    replied_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_inquiries_tenant ON inquiries(tenant_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status, id DESC);
