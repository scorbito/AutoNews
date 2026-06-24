-- 큐: 작업 실행에 필요한 대상 id 목록(process=메시지ids, publish=기사ids). status에 pending/canceled 추가 사용.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS payload TEXT DEFAULT '';
