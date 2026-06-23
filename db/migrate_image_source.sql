-- 이미지 출처(attribution) — 웹 링크에서 가져온 사진은 어디서 왔는지 남긴다.
-- 첨부(HWP/ZIP) 사진은 보도자료 자체 제공이라 보통 비어 있음. Supabase SQL 편집기에서 1회 실행.

ALTER TABLE images ADD COLUMN IF NOT EXISTS source TEXT;
