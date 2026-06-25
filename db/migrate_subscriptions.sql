-- 유료 구독 시스템 (월정액 1플랜 + 월 기사수 한도). 결제(토스 빌링)는 나중에 끼움.
-- 가입 단위 = 기자 개인(tenant 1:1)이므로 tenant_id 기준 1행.

CREATE TABLE IF NOT EXISTS subscriptions (
    tenant_id      BIGINT PRIMARY KEY REFERENCES tenants(id),
    status         TEXT NOT NULL DEFAULT 'inactive',  -- inactive | trialing | active | past_due | canceled
    plan           TEXT NOT NULL DEFAULT 'basic',
    monthly_quota  INTEGER NOT NULL DEFAULT 400,       -- 결제주기당 기사 생성 한도
    period_start   TIMESTAMPTZ,                        -- 현재 주기 시작(한도 카운트 기준선)
    period_end     TIMESTAMPTZ,                        -- 만료 시점(이후 비활성)
    -- 결제 연동용(나중에 토스가 채움) — 비번처럼 Fernet 암호화 저장
    provider       TEXT,                               -- 'toss' 등
    billing_key_enc TEXT,                              -- 정기결제 빌링키(암호화)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 기존 테넌트는 현재 사용이 안 끊기도록 '활성(사실상 무기한)'으로 채움.
-- 결제 붙이기 전까지 운영자가 수동 활성화로 관리.
INSERT INTO subscriptions (tenant_id, status, plan, monthly_quota, period_start, period_end)
SELECT id, 'active', 'basic', 400, now(), now() + interval '100 years'
FROM tenants
ON CONFLICT (tenant_id) DO NOTHING;
