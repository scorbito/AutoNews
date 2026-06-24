-- 기자 화면용 마지막 오류 배너(자동 수집/발행 실패 시 표시, 성공 시 해제)
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ;
