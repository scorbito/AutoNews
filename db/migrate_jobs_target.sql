-- 작업이 처리 중인 대상 메시지 id 목록(쉼표) — 기사함에서 '생성중' 카드 표시용
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS target TEXT DEFAULT '';
