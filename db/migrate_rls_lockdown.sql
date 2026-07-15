-- 보안: public 스키마 테이블 RLS 활성화 (Supabase 경고 rls_disabled_in_public 대응)
--
-- 배경: Supabase는 anon/authenticated 역할에 public 스키마 테이블 전체 권한을 기본 부여하고,
-- PostgREST(https://<project>.supabase.co/rest/v1/)가 그 역할로 항상 열려 있다.
-- anon 키는 브라우저에 노출되는 공개 키(우리도 /reset-password 페이지에서 노출)이므로,
-- RLS가 꺼져 있으면 "누구나 전 테넌트의 메일·기사·설정을 읽고 쓰고 지울 수 있음".
--
-- 우리 앱 영향: 없음. 앱은 DATABASE_URL로 postgres 역할(rolbypassrls=true, 테이블 소유자)로
-- 직접 접속하므로 RLS를 우회한다. 또한 앱은 PostgREST(/rest/v1/)를 전혀 쓰지 않는다
-- (Supabase는 Auth(/auth/v1) + Storage 용도로만 사용).
--
-- 정책(policy)은 만들지 않는다 → RLS on + 정책 0개 = anon/authenticated 전면 차단(기본 거부).

ALTER TABLE public.tenants       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_users  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.articles      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.images        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.drafts        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.folder_state  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inquiries     ENABLE ROW LEVEL SECURITY;

-- 심층 방어: PostgREST를 안 쓰므로 anon/authenticated 권한 자체를 회수한다.
-- (나중에 실수로 RLS를 끄거나 허용 정책을 만들어도 노출되지 않도록)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
