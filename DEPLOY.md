# 배포 가이드 — Railway

auto_news 를 Railway 에 올리는 절차. **1단계(web + cron)** 로 먼저 라이브 → **2단계(worker + 작업큐)** 로 확장.

구성: Railway 에 **컨테이너(서비스)** 를 올리고, **DB·인증·파일저장은 Supabase** 가 담당.
같은 GitHub 저장소에서 서비스마다 **시작 명령만 다르게** 배포한다.

```
Railway 프로젝트
├─ web   : uvicorn (공개 HTTPS 주소)        ← 기자 접속
├─ cron  : run_scheduled.py (예약 호출)      ← 시간 되면 수집·처리
└─ worker: run_worker.py (2단계에서 추가)    ← 무거운 처리 전담
Supabase: Postgres(DB) + Auth + Storage(파일)
```

---

## 0. 사전 준비 (1회)

### 0-1. 운영 시크릿 생성
```bash
# 세션 쿠키 서명 키
python -c "import secrets; print(secrets.token_urlsafe(48))"
# 테넌트 비밀번호 암호화 키 (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
> ⚠️ 개발용 `.env` 의 `SESSION_SECRET`/`CONFIG_ENC_KEY` 를 그대로 쓰지 말 것. **운영용으로 새로 생성**.
> ⚠️ `CONFIG_ENC_KEY` 는 한번 정하면 바꾸지 말 것 — 바꾸면 저장된 메일/CMS 비밀번호를 복호화 못 함.

### 0-2. Supabase DATABASE_URL (Session pooler)
Supabase 대시보드 → **Project Settings → Database → Connection string → "Session pooler"** 복사.
형식: `postgresql://postgres.<ref>:<PW>@aws-0-<region>.pooler.supabase.com:5432/postgres`
> 다중 워커/요청에서 안정적이려면 **Session pooler** 를 써야 함(직접 5432 연결 X).

### 0-3. 파일 저장소 버킷
배포에서는 `STORAGE_BACKEND=supabase`. 버킷을 한 번 만들어 둔다.
- 방법 A: 로컬에서 운영 env 로 `python scripts/init_storage.py`
- 방법 B: Supabase 대시보드 → Storage → 버킷 `files` 생성(Private)

---

## 1. Railway 프로젝트 + web 서비스

1. https://railway.app 가입(GitHub 로그인) → **New Project → Deploy from GitHub repo** → 이 저장소 선택.
2. Railway 가 `railway.json`/`Procfile` 을 읽어 자동 빌드(Nixpacks, Python 3.13). 첫 배포는 실패해도 됨 — 환경변수부터 넣는다.
3. 서비스 → **Variables** 에 아래를 모두 입력:

| 변수 | 값 |
|---|---|
| `DATABASE_URL` | (0-2 의 Session pooler 문자열) |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | (anon public key) |
| `SUPABASE_SERVICE_KEY` | (service role key — 비밀) |
| `SESSION_SECRET` | (0-1 에서 생성) |
| `CONFIG_ENC_KEY` | (0-1 에서 생성, 고정) |
| `ADMIN_EMAILS` | 관리자 이메일(쉼표구분) |
| `GEMINI_API_KEY` | (Gemini 키) |
| `LLM_PROVIDER` | `gemini` |
| `LLM_MODEL` | `gemini-2.5-flash-lite` |
| `GENERATE_MODEL` | `gemini-2.5-flash` |
| `PIPELINE_MODE` | `review` |
| `STORAGE_BACKEND` | `supabase` |
| `STORAGE_BUCKET` | `files` |
| `ATPAJU_LIVE` | (실발행 켤 때만 `1`. 기본 비움 = dry-run) |

4. **Settings → Networking → Generate Domain** 으로 공개 HTTPS 주소 발급.
5. **Deploy** → 주소 접속 → `/login`·`/signup` 확인.

> web 서비스 시작 명령은 `railway.json` 에 이미 지정됨:
> `uvicorn aute_news.web.app:app --host 0.0.0.0 --port $PORT --app-dir src`

---

## 2. cron 서비스 (예약 수집)

1. 같은 프로젝트에서 **New → GitHub Repo**(동일 저장소) 로 서비스 하나 더 추가 → 이름 `cron`.
2. cron 서비스 **Variables** 는 web 과 동일하게(같은 DB/키) — Railway 의 **Shared Variables** 로 묶으면 편함.
3. cron 서비스 **Settings → Deploy**:
   - **Start Command**: `python scripts/run_scheduled.py --window 5`
   - **Cron Schedule**: `*/5 * * * *` (5분마다) — `--window` 값과 맞출 것.
   - (공개 도메인 불필요)
4. 저장 → Railway 가 5분마다 실행, 그 시각에 예약(`collect_times`)이 걸린 테넌트만 수집·처리.

> 지금은 cron 이 수집+처리를 직접 함(동기). 2단계에서 "큐에 넣기"로 바뀜.

---

## 3. 확인 체크리스트
- [ ] `/signup` 으로 가입 → `/settings` 에서 메일 계정 저장 → 폴더 목록 뜸
- [ ] "📥 메일 수집" → 새 메일 들어옴
- [ ] 메일 처리 → 기사 생성(`/` 목록)
- [ ] 이미지가 `/img/...` 로 보임 (= Supabase Storage 연동 정상)
- [ ] cron 로그에 "수집/기사 N건" 출력
- [ ] 발행: 기본 dry-run. 실발행은 §4 참고

---

## 4. 실발행(atpaju) 안전장치
- 코드에 `HARD_DISABLE_LIVE`(atpaju.py) 하드 잠금이 있음 — **이게 True 면 ATPAJU_LIVE 와 무관하게 실발행 안 함**.
- 실제 발행을 켜려면: 잠금 해제 + `ATPAJU_LIVE=1` + 테넌트별 CMS 설정(내 설정 ④) + (옵션)자동 승인요청.
- 운영 안정화 전까지는 **dry-run 유지 권장**.

---

## 2단계 예고 — worker + 작업큐
사용량이 늘면 무거운 처리(수집·LLM·발행)를 web 에서 떼어 **worker 서비스**로 옮긴다.
- `jobs` 테이블(큐) 추가 → web/cron 은 "할 일"만 넣고 즉시 응답
- `worker` 서비스: `python scripts/run_worker.py` 로 큐를 꺼내 처리, **개수만 늘려 확장**
- web 에 "처리중…" 진행률 표시
