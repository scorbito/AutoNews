-- 기사별 SEO/GEO 추천 제목·부제(각 3개) 캐시. Generate LLM이 초안 때 같이 생성.
-- JSON: {"titles":[{"label":"검색","text":"..."},...], "subtitles":[...]}
ALTER TABLE articles ADD COLUMN IF NOT EXISTS seo_suggestions TEXT;
