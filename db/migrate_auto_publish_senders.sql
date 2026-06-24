-- 자동 발행 발신자 화이트리스트: 이 발신자 메일만 자동 발행(비우면 전체 자동)
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS auto_publish_senders TEXT;
