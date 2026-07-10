-- 플랜 2종(베이직 600 / 프로 1,000) + 2026년 가입 고정가(pricing_tier)
-- pricing_tier: 'launch2026'(2026년 내 가입 — 평생 고정가) | 'standard'(정가)

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pricing_tier TEXT;

-- 기존 가입자는 전부 2026년 가입 → 런칭 고정가 부여
UPDATE subscriptions SET pricing_tier = 'launch2026' WHERE pricing_tier IS NULL;

-- 기존 베이직 한도 400 → 600 상향
UPDATE subscriptions SET monthly_quota = 600
 WHERE (plan IS NULL OR plan = 'basic') AND (monthly_quota IS NULL OR monthly_quota = 400);
