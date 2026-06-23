-- 백그라운드 작업 진행상태 (메일 수집 / 기사 생성)
CREATE TABLE IF NOT EXISTS jobs (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   INTEGER NOT NULL,
  user_id     TEXT,
  kind        TEXT NOT NULL,                    -- collect | process
  status      TEXT NOT NULL DEFAULT 'running',  -- running | done | error
  total       INTEGER NOT NULL DEFAULT 0,
  done        INTEGER NOT NULL DEFAULT 0,
  message     TEXT DEFAULT '',
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON jobs(tenant_id, id DESC);
