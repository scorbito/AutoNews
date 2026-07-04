-- 기사 스타일(문체) 설정 — 프리셋 키 또는 'custom'(자유입력)
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS article_style        TEXT;
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS article_style_custom TEXT;
