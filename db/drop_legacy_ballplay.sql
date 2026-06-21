-- ============================================================
-- 옛 ballplay(bet) 앱 테이블 정리 스크립트
-- ============================================================
-- ⚠️ 경고: 되돌릴 수 없습니다. 실행하면 ballplay 데이터가 영구 삭제됩니다.
--    ballplay/bet 앱을 더 이상 쓰지 않는 게 확실할 때만 실행하세요.
--    AutoNews 테이블(messages/attachments/articles/drafts/images/folder_state)은
--    아래 목록에 없으므로 영향받지 않습니다.
--
-- 사용법: Supabase 대시보드 → SQL Editor → 붙여넣기 → Run
-- (안전하게 하려면 실행 전 Database → Backups 에서 백업/스냅샷 권장)
-- ============================================================

DROP TABLE IF EXISTS
    match_emotion_timeline,
    emotions_aggregate,
    emotions_raw,
    team_rankings,
    games,
    teams,
    news,
    users,
    cron_locks
CASCADE;

-- 삭제 확인: 남은 public 테이블 목록 보기
-- SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;
