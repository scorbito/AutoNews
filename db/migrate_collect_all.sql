-- 테스트용 '기간 무시 전체 수집' 토글 — 지정 폴더의 모든 메일을 날짜 상관없이 수집.
-- 중복은 messages.UNIQUE(tenant_id, message_id)로 자동 제외. Supabase SQL 편집기에서 1회 실행.

ALTER TABLE user_mail_config ADD COLUMN IF NOT EXISTS collect_all INTEGER NOT NULL DEFAULT 0;
