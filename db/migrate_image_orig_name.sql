-- 이미지 원본 파일명 보존 컬럼 — ZIP 등에서 푼 사진의 원래 이름.
-- 저장키(path)는 ASCII로 두고, 한글 파일명은 orig_name 에 보존해
-- LLM 사진↔기사 매칭과 번호(7-2.) 힌트에 쓴다. Supabase SQL 편집기에서 1회 실행.

ALTER TABLE images ADD COLUMN IF NOT EXISTS orig_name TEXT;
