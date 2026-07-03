-- 스레드 병합 대비 — 메일 스레드 헤더 저장(In-Reply-To / References)
-- Message-ID 는 이미 messages.message_id 로 저장 중. 이 둘로 답장 체인을 재구성한다.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS in_reply_to    TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS mail_references TEXT;
