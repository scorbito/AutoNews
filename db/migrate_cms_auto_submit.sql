-- atpaju 저장 후 자동 승인요청(작성중→승인요청) 옵션. Supabase SQL 편집기에서 1회 실행.
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS cms_auto_submit INTEGER NOT NULL DEFAULT 0;
