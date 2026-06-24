-- 보관함: 메일을 보관 상태로(아카이브 시각). NULL=활성(기사함), 값 있음=보관함.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_messages_archived ON messages(tenant_id, archived_at);
