-- 토스 빌링(정기결제) 연동용 컬럼 추가.
-- customer_key: 토스 구매자 고유 ID(테넌트별 1개, 비추측 랜덤)
-- charges_count: 누적 결제 횟수(0이면 다음 결제는 첫 달 반값)

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS customer_key   TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS charges_count  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_paid_at   TIMESTAMPTZ;
