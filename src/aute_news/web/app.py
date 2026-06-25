"""기자 검토 UI (FastAPI + Jinja2) — Supabase Auth 로그인 + 멀티테넌트.

로그인한 신문사(tenant)의 데이터만 보이고 처리된다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import admin, articlegen, auth, db, subscription, notify
from ..config import CATEGORY_CODES
from ..generator import generate_article, render_markdown
from ..pipeline import publish_article
from ..publishers import get_publisher
from ..storage import get_storage, mime_for

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="뉴스플로우 AI 기자 검토")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def _nav_counts(request: Request) -> dict:
    """사이드바 메뉴 옆 개수(수집함=메일 수, 기사=기사 수). 비로그인/실패 시 빈 dict."""
    tid = request.session.get("tenant_id")
    if not tid:
        return {}
    try:
        conn = db.connect()
        row = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE archived_at IS NULL) active, "
            "COUNT(*) FILTER (WHERE archived_at IS NOT NULL) arch "
            "FROM messages WHERE tenant_id=?", (tid,)).fetchone()
        conn.close()
        return {"messages": row["active"], "archived": row["arch"]}
    except Exception:  # noqa: BLE001 (개수 표시는 부가기능 — 실패해도 화면은 떠야 함)
        return {}


templates.env.globals["nav_counts"] = _nav_counts


def _job_queue(request: Request) -> list:
    """사이드바 작업 큐(진행중+대기중) + 대상 메일 제목(말줄임용). 실패 시 빈 목록."""
    tid = request.session.get("tenant_id")
    if not tid:
        return []
    try:
        conn = db.connect()
        rows = [dict(r) for r in db.queue_jobs(conn, tid)]
        # 대상 메시지 제목 일괄 조회
        all_ids = set()
        for r in rows:
            all_ids.update(int(x) for x in (r.get("target") or "").split(",") if x.strip().isdigit())
        submap = {}
        if all_ids:
            ph = ",".join(["?"] * len(all_ids))
            for m in conn.execute(
                    f"SELECT id, subject FROM messages WHERE tenant_id=? AND id IN ({ph})",
                    (tid, *all_ids)).fetchall():
                submap[m["id"]] = m["subject"] or "(제목 없음)"
        conn.close()
        for r in rows:
            tids = [int(x) for x in (r.get("target") or "").split(",") if x.strip().isdigit()]
            if not tids:
                r["subject"] = ""
            elif len(tids) == 1:
                r["subject"] = submap.get(tids[0], "")
            else:
                first = submap.get(tids[0], "")
                r["subject"] = f"{first} 외 {len(tids) - 1}건" if first else f"{len(tids)}건"
        return rows
    except Exception:  # noqa: BLE001
        return []


templates.env.globals["job_queue"] = _job_queue


def _static_ver() -> str:
    """app.css 수정시각 → 캐시 버스팅 버전(브라우저가 옛 CSS 캐시하는 문제 방지)."""
    try:
        return str(int((BASE / "static" / "app.css").stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["static_ver"] = _static_ver()

STATUS_LABEL = {None: "미생성", "draft": "초안", "reviewed": "검토완료", "published": "발행됨"}
ASTATUS = {None: "-", "split": "분할됨", "drafted": "초안",
           "reviewed": "검토완료", "published": "발행됨"}
AFILTERS = [("all", "전체"), ("drafted", "초안"), ("reviewed", "검토완료"),
            ("published", "발행됨"), ("split", "분할만")]
FILTERS = [("all", "전체"), ("none", "미생성"), ("draft", "초안"),
           ("reviewed", "검토완료"), ("published", "발행됨")]

_PUBLIC_PATHS = ("/login", "/logout", "/signup")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if not request.session.get("tenant_id"):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# SessionMiddleware 를 나중에 추가 → 가장 바깥 → 세션이 위 인증게이트보다 먼저 채워짐
app.add_middleware(SessionMiddleware,
                   secret_key=os.getenv("SESSION_SECRET", "dev-insecure-change-me"),
                   max_age=60 * 60 * 12)


def _tenant(request: Request) -> int:
    return request.session["tenant_id"]


def _jload(s):
    try:
        return json.loads(s) if s else {}
    except (ValueError, TypeError):
        return {}


def md_to_html(text: str) -> str:
    import html
    out, in_list = [], False
    for line in (text or "").splitlines():
        s = line.rstrip()
        if s.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{html.escape(s[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if s.startswith("# "):
            out.append(f"<h1>{html.escape(s[2:])}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2 class='sub'>{html.escape(s[3:])}</h2>")
        elif s.startswith("■"):
            out.append(f"<p class='block'>{html.escape(s)}</p>")
        elif s:
            out.append(f"<p>{html.escape(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# ── 인증 ──────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    user = auth.supabase_login(email, password)
    if not user:
        return RedirectResponse("/login?error=1", status_code=303)
    conn = db.connect()
    tid, role = auth.tenant_for_user(conn, user["id"])
    conn.close()
    if not tid:
        return RedirectResponse("/login?error=2", status_code=303)
    request.session.update({"user_id": user["id"], "email": user["email"],
                            "tenant_id": tid, "role": role,
                            "is_admin": admin.is_admin(user["email"])})
    return RedirectResponse("/inbox", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "signup.html", {"error": error})


@app.post("/signup")
def signup_post(request: Request, paper: str = Form(...), email: str = Form(...),
                password: str = Form(...), password2: str = Form(...)):
    """셀프 가입 — 신문사(tenant) + 사용자 생성 후 자동 로그인 → 메일 설정으로."""
    paper, email = paper.strip(), email.strip()
    if len(password) < 6:
        return RedirectResponse("/signup?error=pw", status_code=303)
    if password != password2:
        return RedirectResponse("/signup?error=mismatch", status_code=303)
    conn = db.connect()
    try:
        tid, uid = admin.create_account(conn, paper, email, password)
    except Exception as e:  # noqa: BLE001 (이메일 중복 등)
        conn.close()
        import sys
        print(f"[signup] 가입 실패: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        detail = "exists" if "registered" in str(e).lower() or "exists" in str(e).lower() else "fail"
        return RedirectResponse(f"/signup?error={detail}", status_code=303)
    conn.close()
    # 가입 직후 바로 로그인 상태로 (셀프서비스: 가입→로그인→메일설정)
    request.session.update({"user_id": uid, "email": email, "tenant_id": tid,
                            "role": "editor", "is_admin": admin.is_admin(email)})
    return RedirectResponse("/settings?welcome=1", status_code=303)


# ── 관리자(SaaS 운영자) ───────────────────────────────
def _require_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, msg: str = ""):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    conn = db.connect()
    tenants = admin.list_tenants(conn)
    conn.close()
    return templates.TemplateResponse(
        request, "admin.html", {"tenants": tenants, "cats": CATEGORY_CODES, "msg": msg})


@app.post("/admin/tenant")
def admin_create(request: Request, name: str = Form(...), email: str = Form(...),
                 password: str = Form(...)):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    conn = db.connect()
    try:
        tid, _ = admin.create_account(conn, name, email, password)
        msg = f"신문사 '{name}' 생성 (tenant {tid}, {email})"
    except Exception as e:  # noqa: BLE001
        msg = f"실패: {e}"
    conn.close()
    return RedirectResponse(f"/admin?msg={msg}", status_code=303)


@app.post("/admin/tenant/{tid}/delete")
def admin_delete_tenant(request: Request, tid: int):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    conn = db.connect()
    try:
        res = admin.delete_tenant(conn, tid)
        msg = f"신문사 {tid} 삭제 (파일 {res['files']}개, 계정 {res['users']}개)"
    except Exception as e:  # noqa: BLE001
        msg = f"삭제 실패: {e}"
    conn.close()
    return RedirectResponse(f"/admin?msg={msg}", status_code=303)


@app.post("/admin/user/{user_id}/mail")
async def admin_user_mail(request: Request, user_id: str):
    """관리자가 기자 메일 계정을 대신 설정."""
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    from ..collector import host_for_email
    form = await request.form()
    conn = db.connect()
    row = conn.execute("SELECT tenant_id FROM tenant_users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return RedirectResponse("/admin?msg=사용자 없음", status_code=303)
    email = (form.get("imap_email") or "").strip()
    host = (form.get("imap_host") or "").strip() or host_for_email(email)
    db.set_user_mail(
        conn, user_id, row["tenant_id"],
        imap_email=email, imap_host=host,
        imap_folders=(form.get("imap_folders") or "").strip(),
        collect_enabled=1 if form.get("collect_enabled") == "1" else 0,
        imap_password=(form.get("imap_password") or None))
    conn.close()
    return RedirectResponse(f"/admin?msg={email} 메일 설정 저장", status_code=303)


@app.post("/admin/config/{tid}")
async def admin_config(request: Request, tid: int):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    form = await request.form()
    kw = {k: v for k, v in form.items() if v not in (None, "")}
    if "collect_enabled" in kw:
        kw["collect_enabled"] = int(kw["collect_enabled"])  # INTEGER 컬럼
    kw["cms_auto_submit"] = 1 if form.get("cms_auto_submit") == "1" else 0  # 체크박스(미체크=0)
    conn = db.connect()
    db.set_tenant_config(conn, tid, **kw)
    conn.close()
    return RedirectResponse(f"/admin?msg=테넌트 {tid} 설정 저장", status_code=303)


@app.post("/admin/tenant/{tid}/subscription")
async def admin_subscription(request: Request, tid: int):
    """구독 수동 활성화/연장·비활성화(결제 붙기 전 운영자용). 나중에 결제 콜백이 대체."""
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    form = await request.form()
    conn = db.connect()
    if form.get("action") == "deactivate":
        subscription.deactivate(conn, tid)
        msg = f"테넌트 {tid} 구독 비활성화"
    else:
        days = int(form.get("days") or subscription.PERIOD_DAYS)
        quota = int(form.get("quota") or subscription.DEFAULT_QUOTA)
        subscription.activate(conn, tid, days=days, quota=quota)
        msg = f"테넌트 {tid} 구독 활성화 ({days}일 · 한도 {quota})"
    conn.close()
    return RedirectResponse(f"/admin?msg={msg}", status_code=303)


# ── 기사(articles) ────────────────────────────────────
def _group_by_email(arts: list) -> list:
    """기사 목록을 출처 메일별 묶음으로(순서 유지). [{subject, date, count, articles:[]}]."""
    groups, index = [], {}
    for a in arts:
        key = a["email_id"]
        if key not in index:
            index[key] = len(groups)
            groups.append({"email_id": key, "subject": a["email_subject"],
                           "date": a["email_date"], "articles": []})
        groups[index[key]]["articles"].append(a)
    return groups


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # 수집함+기사 통합 → 기사함(/inbox) 하나로
    return RedirectResponse("/inbox", status_code=303)


@app.post("/articles/bulk")
def articles_bulk(request: Request, background: BackgroundTasks, action: str = Form(...),
                  ids: Annotated[list[int], Form()] = []):
    """검토완료(동기·빠름) / 발행(백그라운드)."""
    t = _tenant(request)
    if not ids:
        return RedirectResponse("/inbox", status_code=303)
    if action == "review":
        conn = db.connect()
        for aid in ids:
            db.set_article_status(conn, aid, "reviewed", tenant_id=t)
        conn.close()
        return RedirectResponse("/inbox", status_code=303)
    if action == "publish":
        conn = db.connect()
        target = _msg_ids_for_articles(conn, t, list(ids))
        conn.close()
        _enqueue(request, background, "publish", len(ids),
                 payload=",".join(str(i) for i in ids), target=target)
    return RedirectResponse("/inbox", status_code=303)


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request, msg: str = ""):
    """보관함 — 메일 수집 시 옮겨진 이전 메일·기사(7일 후 자동 삭제)."""
    return _message_view(request, msg, archived=True)


@app.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, msg: str = ""):
    """기사함 — 새로 수집된 메일 + 그 메일이 생성한 기사."""
    return _message_view(request, msg, archived=False)


def _message_view(request: Request, msg: str, archived: bool):
    t = _tenant(request)
    conn = db.connect()
    msgs = db.list_messages(conn, tenant_id=t, archived=archived)
    arts = db.list_all_articles(conn, "all", tenant_id=t)
    # 진행중+대기중 작업을 한 번에 조회(연결·왕복 절감) → 아래서 active/pending 으로 분리
    job_rows = conn.execute(
        "SELECT id, kind, status, target, "
        "(status='running' AND updated_at > now() - interval '15 minutes') AS fresh_running "
        "FROM jobs WHERE tenant_id=? AND status IN ('running','pending') ORDER BY id", (t,)).fetchall()
    last_error = None
    if not archived:                                   # 기사함에만 오류 배너(기자 액션 필요)
        last_error = (db.get_tenant_config(conn, t) or {}).get("last_error")
    sub = subscription.status_view(conn, t)
    conn.close()
    running = [r for r in job_rows if r["status"] == "running" and r["fresh_running"]]
    active = running[-1] if running else None       # 최신(가장 큰 id) 진행중 작업
    # 현재 처리 중인 메시지 id(있으면 그 카드 버튼을 '생성중/발행중'으로)
    processing_ids: set = set()
    processing_label, processing_cls = "생성중…", "green"
    if active and active["kind"] in ("process", "publish") and active["target"]:
        processing_ids = {int(x) for x in active["target"].split(",") if x.strip().isdigit()}
        if active["kind"] == "publish":
            processing_label, processing_cls = "발행중…", "publish"
    # 대기(pending) 작업의 대상 메일 → 카드에 '대기중(취소)' 버튼
    pend_rows = [r for r in job_rows if r["status"] == "pending"]
    pending_map: dict = {}
    for j in pend_rows:
        for x in (j["target"] or "").split(","):
            if x.strip().isdigit():
                pending_map.setdefault(int(x), {"job_id": j["id"], "kind": j["kind"]})
    by_msg: dict = {}
    for a in arts:
        by_msg.setdefault(a["email_id"], []).append(a)
    total_arts = 0
    for m in msgs:
        m["articles"] = by_msg.get(m["id"], [])
        total_arts += len(m["articles"])
    return templates.TemplateResponse(
        request, "inbox.html",
        {"msgs": msgs, "msg": msg, "total_arts": total_arts, "processing_ids": processing_ids,
         "processing_label": processing_label, "processing_cls": processing_cls,
         "pending_map": pending_map, "archived": archived, "last_error": last_error,
         "sub": sub, "astatus": ASTATUS, "cats": CATEGORY_CODES})


@app.get("/messages/{message_id}", response_class=HTMLResponse)
def message_detail(request: Request, message_id: int):
    """수집된 메일 1건 상세 — 본문·첨부 추출 텍스트·사진(처리 전 미리보기)."""
    t = _tenant(request)
    conn = db.connect()
    msg = conn.execute("SELECT * FROM messages WHERE id=? AND tenant_id=?",
                       (message_id, t)).fetchone()
    if not msg:
        conn.close()
        return HTMLResponse("메일을 찾을 수 없습니다.", 404)
    att_rows = conn.execute(
        "SELECT * FROM attachments WHERE message_pk=? AND tenant_id=? ORDER BY id",
        (message_id, t)).fetchall()
    atts = [{"row": a, "images": db.list_images(conn, a["id"], tenant_id=t)} for a in att_rows]
    art_count = conn.execute(
        """SELECT COUNT(*) c FROM articles ar JOIN attachments a ON a.id=ar.attachment_id
           WHERE a.message_pk=? AND ar.tenant_id=?""", (message_id, t)).fetchone()["c"]
    conn.close()
    return templates.TemplateResponse(
        request, "message_detail.html", {"m": msg, "atts": atts, "art_count": art_count})


# ── 작업 큐 (수집/생성/발행을 순차 처리) ──────────────
def _msg_ids_for_articles(conn, tenant_id: int, ids: list[int]) -> str:
    """기사 id 목록 → 그 기사들이 속한 메시지 id(쉼표). 카드 '발행중' 표시용."""
    if not ids:
        return ""
    ph = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"""SELECT DISTINCT a.message_pk m FROM articles ar
            JOIN attachments a ON a.id=ar.attachment_id
            WHERE ar.tenant_id=? AND ar.id IN ({ph}) AND a.message_pk IS NOT NULL""",
        (tenant_id, *ids)).fetchall()
    return ",".join(str(r["m"]) for r in rows)


def _ids(s: str) -> list[int]:
    return [int(x) for x in (s or "").split(",") if x.strip().isdigit()]


def _execute_job(conn, job) -> None:
    """큐에서 꺼낸 작업 1건 실행(kind별). 진행률을 갱신하고 done/error 로 마감."""
    jid, kind, tid = job["id"], job["kind"], job["tenant_id"]
    ids = _ids(job["payload"])
    try:
        if kind == "collect":
            from ..collector import collect_for_user
            # 보관 이동·정리는 collect_for_user 내부에서 수행(web·cron 동일 동작)
            stats = collect_for_user(job["user_id"])
            if stats.get("skipped"):
                msg = f"수집 불가: {stats['skipped']} (내 설정에서 메일 계정 등록)"
            elif stats.get("baselined") and not stats.get("new_messages"):
                msg = "메일함 기준선을 '오늘 0시'로 설정 — 이후 도착분부터 수집. (오늘 새 메일 없음)"
            else:
                msg = f"수집 완료 — 새 메일 {stats.get('new_messages', 0)}건, 첨부 {stats.get('attachments', 0)}개"
            db.update_job(conn, jid, status="done", message=msg)
        elif kind == "process":
            from ..pipeline import process_message
            made = done = 0
            for mid in ids:
                try:
                    # 사람이 직접 '기사 생성'을 누른 것 → 트리아지 SKIP 무시하고 강제 생성
                    made += len(process_message(conn, mid, mode="review", tenant_id=tid,
                                                force=True).get("articles", []))
                except Exception as e:  # noqa: BLE001
                    notify.report_failure("기사 생성", tid, exc=e, detail=f"메일 {mid}: {type(e).__name__}: {e}")
                done += 1
                db.update_job(conn, jid, done=done, message=f"기사 생성 {done}/{len(ids)} … 누적 {made}건")
            db.update_job(conn, jid, status="done", message=f"완료 — 메일 {done}건 · 기사 {made}건")
        elif kind == "publish":
            ok = done = 0
            for aid in ids:
                try:
                    res = publish_article(conn, aid, tenant_id=tid)
                    if res and getattr(res, "ok", False):
                        ok += 1
                except Exception as e:  # noqa: BLE001
                    notify.report_failure("발행", tid, exc=e, detail=f"기사 {aid}: {type(e).__name__}: {e}")
                done += 1
                db.update_job(conn, jid, done=done, message=f"발행 {done}/{len(ids)} … 성공 {ok}건")
            db.update_job(conn, jid, status="done", message=f"발행 완료 — {ok}/{len(ids)}건")
    except Exception as e:  # noqa: BLE001
        db.update_job(conn, jid, status="error", message=f"{kind} 실패: {type(e).__name__}")
        notify.report_failure(f"작업({kind})", tid, exc=e)


def _drain(tenant_id: int) -> None:
    """테넌트 대기열을 순차 처리. 권고잠금으로 드레이너 단일화, 종료 직전 재확인으로 누락 방지."""
    conn = db.connect()
    try:
        while True:
            if not db.try_drain_lock(conn, tenant_id):
                return                                   # 다른 드레이너가 처리 중
            try:
                while True:
                    job = db.claim_next_job(conn, tenant_id)
                    if not job:
                        break
                    _execute_job(conn, job)
            finally:
                db.drain_unlock(conn, tenant_id)
            if not db.has_pending(conn, tenant_id):      # 잠금 푼 사이 들어온 작업 회수
                return
    finally:
        conn.close()


def _enqueue(request: Request, background: BackgroundTasks, kind: str, total: int,
             payload: str = "", target: str = "") -> str | None:
    """작업을 대기열에 넣고 드레이너를 깨운다(이미 돌면 알아서 이어받음).

    구독 게이트: 비용 드는 작업(collect/process)은 구독활성+한도여유일 때만.
    막히면 적재하지 않고 안내 문구를 반환(허용/중복이면 None)."""
    t = _tenant(request)
    conn = db.connect()
    if kind in ("collect", "process"):
        ok, reason = subscription.can_use(conn, t)
        if not ok:
            conn.close()
            return subscription.block_message(reason)
    init = {"collect": "메일 수집 중…", "process": "기사 생성 준비 중…",
            "publish": "발행 준비 중…"}.get(kind, "처리 중…")
    if db.job_exists(conn, t, kind, payload):   # 같은 작업이 이미 대기/진행 중 → 중복 적재 방지
        conn.close()
        return None
    db.create_job(conn, t, request.session.get("user_id"), kind, total=total,
                  message=init, payload=payload, target=target, status="pending")
    conn.close()
    background.add_task(_drain, t)
    return None


@app.post("/collect")
def collect_now(request: Request, background: BackgroundTasks):
    """기자 본인 메일함 수집 — 큐에 적재."""
    blocked = _enqueue(request, background, "collect", 0)
    return RedirectResponse(f"/inbox?msg={blocked}" if blocked else "/inbox", status_code=303)


@app.post("/messages/bulk-process")
def messages_bulk_process(request: Request, background: BackgroundTasks,
                          ids: Annotated[list[int], Form()] = []):
    if not ids:
        return RedirectResponse("/inbox", status_code=303)
    p = ",".join(str(i) for i in ids)
    blocked = _enqueue(request, background, "process", len(ids), payload=p, target=p)
    return RedirectResponse(f"/inbox?msg={blocked}" if blocked else "/inbox", status_code=303)


@app.post("/messages/{message_id}/process")
def message_process(request: Request, message_id: int, background: BackgroundTasks):
    blocked = _enqueue(request, background, "process", 1, payload=str(message_id), target=str(message_id))
    return RedirectResponse(f"/inbox?msg={blocked}" if blocked else "/inbox", status_code=303)


@app.post("/messages/process-all")
def messages_process_all(request: Request, background: BackgroundTasks):
    """아직 기사 안 만든 메일 전체를 큐에 적재."""
    t = _tenant(request)
    conn = db.connect()
    rows = conn.execute(
        """SELECT m.id FROM messages m WHERE m.tenant_id=? AND NOT EXISTS (
               SELECT 1 FROM articles ar JOIN attachments a ON a.id=ar.attachment_id
               WHERE a.message_pk=m.id) ORDER BY m.id""", (t,)).fetchall()
    conn.close()
    ids = [r["id"] for r in rows]
    if not ids:
        return RedirectResponse("/inbox?msg=처리할 미처리 메일이 없습니다", status_code=303)
    p = ",".join(str(i) for i in ids)
    blocked = _enqueue(request, background, "process", len(ids), payload=p, target=p)
    return RedirectResponse(f"/inbox?msg={blocked}" if blocked else "/inbox", status_code=303)


@app.post("/messages/{message_id}/archive")
def message_archive(request: Request, message_id: int):
    """이 메일을 보관함으로 이동(수동)."""
    t = _tenant(request)
    conn = db.connect()
    conn.execute("UPDATE messages SET archived_at=now() WHERE id=? AND tenant_id=? AND archived_at IS NULL",
                 (message_id, t))
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer") or "/inbox", status_code=303)


@app.post("/jobs/{job_id}/cancel")
def job_cancel(request: Request, job_id: int):
    """대기(pending) 작업 취소. 진행중은 취소 불가."""
    conn = db.connect()
    db.cancel_job(conn, _tenant(request), job_id)
    conn.close()
    back = request.headers.get("referer") or "/inbox"
    return RedirectResponse(back, status_code=303)


@app.get("/jobs/active")
def jobs_active(request: Request):
    """폴링용 — 진행중 작업 + 현재 실행 job id + 대기 수. 멈춘 running 은 오류로."""
    t = _tenant(request)
    conn = db.connect()
    run = db.active_job(conn, t)
    pend = conn.execute("SELECT COUNT(*) c FROM jobs WHERE tenant_id=? AND status='pending'",
                        (t,)).fetchone()["c"]
    conn.close()
    if run:
        return {"status": "running", "job_id": run["id"], "kind": run["kind"],
                "total": run["total"], "done": run["done"], "message": run["message"],
                "pending": pend}
    conn = db.connect()
    j = db.latest_job(conn, t)
    conn.close()
    if not j:
        return {"status": "none", "pending": pend}
    if j["status"] == "running" and j["stale"]:
        return {"status": "error", "message": "작업이 응답하지 않습니다(시간 초과)."}
    return {"status": j["status"], "message": j["message"], "pending": pend}


@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request, mailerr: str = "", welcome: str = ""):
    from ..collector import list_imap_folders
    uid = request.session["user_id"]
    conn = db.connect()
    cfg = db.get_tenant_config(conn, _tenant(request)) or {}
    mail = db.get_user_mail(conn, uid) or {}
    conn.close()
    # 메일 계정이 등록돼 있으면 라이브로 폴더 목록을 받아 체크박스로 보여줌
    folders, folder_err = [], ""
    if mail.get("imap_host") and mail.get("imap_email") and mail.get("imap_password"):
        try:
            folders = list_imap_folders(mail["imap_host"], mail["imap_email"], mail["imap_password"])
        except Exception as e:  # noqa: BLE001
            folder_err = f"폴더 목록을 못 받았습니다: {type(e).__name__} (계정/비번 확인)"
    selected = {f.strip() for f in (mail.get("imap_folders") or "").split(",") if f.strip()}
    auto_on = bool(cfg.get("collect_enabled")) and (cfg.get("pipeline_mode") == "auto")
    return templates.TemplateResponse(
        request, "settings.html",
        {"auto_on": auto_on, "collect_times": cfg.get("collect_times") or "",
         "auto_publish_senders": cfg.get("auto_publish_senders") or "",
         "mail": mail, "folders": folders, "selected": selected,
         "folder_err": folder_err, "mailerr": mailerr,
         "mail_enabled": bool(mail.get("collect_enabled")),
         "cms_auto_submit": bool(cfg.get("cms_auto_submit")),
         "publisher": cfg.get("publisher") or "html",
         "ndsoft_base_url": cfg.get("ndsoft_base_url") or "",
         "cms_user": cfg.get("cms_user") or "",
         "cms_user_email": cfg.get("cms_user_email") or "",
         "cms_section": cfg.get("cms_section") or "",
         "has_cms_password": bool(cfg.get("cms_password")),
         "welcome": welcome == "1", "email": request.session.get("email")})


@app.post("/settings/mail")
def settings_mail(request: Request, imap_email: str = Form(...),
                  imap_password: str = Form(""), imap_host: str = Form("")):
    """기자 본인 메일 계정 저장(비번은 입력했을 때만 갱신)."""
    from ..collector import host_for_email
    host = imap_host.strip() or host_for_email(imap_email)
    conn = db.connect()
    db.set_user_mail(conn, request.session["user_id"], _tenant(request),
                     imap_email=imap_email.strip(), imap_host=host,
                     imap_password=imap_password or None)
    conn.close()
    err = "" if host else "도메인을 알 수 없어 IMAP 호스트를 입력해야 합니다."
    return RedirectResponse(f"/settings?mailerr={err}", status_code=303)


@app.post("/settings/folders")
async def settings_folders(request: Request):
    """선택한 수집 폴더 저장. (예약 수집은 자동 모드 테넌트의 모든 계정을 수집 — 계정별 토글 없음)"""
    form = await request.form()
    folders = ",".join(form.getlist("folders"))
    collect_all = 1 if form.get("collect_all") == "1" else 0
    conn = db.connect()
    db.set_user_mail(conn, request.session["user_id"], _tenant(request),
                     imap_folders=folders, collect_enabled=1, collect_all=collect_all)
    conn.close()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings")
def settings_save(request: Request, auto_mode: str = Form("0"), collect_times: str = Form(""),
                  auto_publish_senders: str = Form("")):
    on = auto_mode == "1"
    conn = db.connect()
    db.set_tenant_config(conn, _tenant(request),
                         collect_enabled=1 if on else 0,
                         pipeline_mode="auto" if on else "review",
                         collect_times=collect_times.strip(),
                         auto_publish_senders=auto_publish_senders.strip())
    conn.close()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/cms")
def settings_cms(request: Request, publisher: str = Form("html"),
                 ndsoft_base_url: str = Form(""), cms_user: str = Form(""),
                 cms_password: str = Form(""), cms_user_email: str = Form(""),
                 cms_section: str = Form(""), cms_auto_submit: str = Form("0")):
    """기자(신문사) 본인이 CMS 발행 설정을 직접 저장 — 셀프 온보딩."""
    conn = db.connect()
    db.set_tenant_config(conn, _tenant(request),
                         publisher=publisher or "html",
                         ndsoft_base_url=ndsoft_base_url.strip(),
                         cms_user=cms_user.strip(),
                         cms_user_email=cms_user_email.strip(),
                         cms_section=cms_section.strip(),
                         cms_auto_submit=1 if cms_auto_submit == "1" else 0,
                         cms_password=(cms_password or None))
    conn.close()
    return RedirectResponse("/settings", status_code=303)


# ── 구독·결제 ─────────────────────────────────────────
@app.get("/billing", response_class=HTMLResponse)
def billing(request: Request, msg: str = ""):
    """내 구독 상태·한도 사용량 + 결제(준비 중). 결제 연동은 나중에 이 자리에."""
    conn = db.connect()
    sub = subscription.status_view(conn, _tenant(request))
    conn.close()
    return templates.TemplateResponse(
        request, "billing.html",
        {"sub": sub, "msg": msg, "email": request.session.get("email")})


@app.get("/a/{article_id}", response_class=HTMLResponse)
def article_detail(request: Request, article_id: int):
    t = _tenant(request)
    conn = db.connect()
    art = db.get_article(conn, article_id, tenant_id=t)
    imgs = db.list_article_images(conn, article_id, tenant_id=t)
    conn.close()
    return templates.TemplateResponse(
        request, "article_detail.html",
        {"a": art, "images": imgs, "astatus": ASTATUS, "cats": CATEGORY_CODES,
         "source_info": _jload(art["source_info"]) if art else {},
         "editor_notes": _jload(art["editor_notes"]) if art else {}})


@app.post("/a/{article_id}/save")
def article_save(request: Request, article_id: int, headline: str = Form(...),
                 subtitle: str = Form(""), content_html: str = Form(...),
                 category_code: str = Form("S1N10")):
    conn = db.connect()
    db.update_article_edit(conn, article_id, headline=headline, subtitle=subtitle,
                           content_html=content_html, category_code=category_code,
                           tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.post("/a/{article_id}/image/{image_id}/remove")
def article_image_remove(request: Request, article_id: int, image_id: int):
    """기사에서 사진 1장 제거(로고·오매칭 등). article_id 연결만 해제."""
    conn = db.connect()
    db.assign_image_article(conn, image_id, None, tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.post("/a/{article_id}/review")
def article_review(request: Request, article_id: int):
    conn = db.connect()
    db.set_article_status(conn, article_id, "reviewed", tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.post("/a/{article_id}/publish")
def article_publish(request: Request, article_id: int, background: BackgroundTasks):
    """기사 1건 발행 — 큐에 적재. 진행은 기사함에서 표시."""
    conn = db.connect()
    target = _msg_ids_for_articles(conn, _tenant(request), [article_id])
    conn.close()
    _enqueue(request, background, "publish", 1, payload=str(article_id), target=target)
    return RedirectResponse("/inbox", status_code=303)


# ── 발행 미리보기 게시판 (테스트/데모용 신문 페이지) ──
@app.get("/board", response_class=HTMLResponse)
def board(request: Request):
    """발행된 기사를 신문 사이트처럼 보여주는 미리보기 게시판."""
    t = _tenant(request)
    conn = db.connect()
    arts = db.list_all_articles(conn, "published", tenant_id=t)
    tname = conn.execute("SELECT name FROM tenants WHERE id=?", (t,)).fetchone()
    conn.close()
    return templates.TemplateResponse(
        request, "board.html",
        {"arts": arts, "cats": CATEGORY_CODES,
         "paper": tname["name"] if tname else "신문"})


@app.get("/board/{article_id}", response_class=HTMLResponse)
def board_article(request: Request, article_id: int):
    """발행된 기사 1건을 기사 페이지처럼 렌더."""
    t = _tenant(request)
    conn = db.connect()
    art = db.get_article(conn, article_id, tenant_id=t)
    imgs = db.list_article_images(conn, article_id, tenant_id=t) if art else []
    tname = conn.execute("SELECT name FROM tenants WHERE id=?", (t,)).fetchone()
    conn.close()
    if not art or art["status"] != "published":
        return HTMLResponse("발행된 기사가 아닙니다.", 404)
    return templates.TemplateResponse(
        request, "board_article.html",
        {"a": art, "images": imgs, "cats": CATEGORY_CODES,
         "paper": tname["name"] if tname else "신문"})


@app.get("/img/{image_id}")
def serve_img(request: Request, image_id: int):
    t = _tenant(request)
    conn = db.connect()
    r = conn.execute("SELECT path, ext FROM images WHERE id=? AND tenant_id=?", (image_id, t)).fetchone()
    conn.close()
    if not r or not r["path"]:
        return HTMLResponse("not found", 404)
    data = get_storage().get(r["path"])
    if data is None:
        return HTMLResponse("not found", 404)
    return Response(content=data, media_type=mime_for(r["ext"]))


# ── 레거시(첨부 기준 drafts) ──────────────────────────
@app.get("/legacy", response_class=HTMLResponse)
def index_legacy(request: Request, status: str = "all", msg: str = ""):
    t = _tenant(request)
    conn = db.connect()
    items = db.list_items(conn, None if status == "all" else status, tenant_id=t)
    counts = db.status_counts(conn, tenant_id=t)
    conn.close()
    return templates.TemplateResponse(
        request, "list.html",
        {"items": items, "label": STATUS_LABEL, "filters": FILTERS,
         "counts": counts, "active": status, "msg": msg})


@app.get("/item/{att_id}", response_class=HTMLResponse)
def item(request: Request, att_id: int):
    t = _tenant(request)
    conn = db.connect()
    row = db.get_item(conn, att_id, tenant_id=t)
    imgs = db.list_images(conn, att_id, tenant_id=t)
    conn.close()
    return templates.TemplateResponse(
        request, "detail.html", {"it": row, "images": imgs, "label": STATUS_LABEL})


@app.post("/image/{image_id}/toggle")
def toggle_img(request: Request, image_id: int, att_id: int = Form(...), selected: int = Form(...)):
    conn = db.connect()
    db.set_image_selected(conn, image_id, bool(selected), tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


@app.get("/article/{att_id}", response_class=HTMLResponse)
def article(request: Request, att_id: int):
    t = _tenant(request)
    conn = db.connect()
    row = db.get_item(conn, att_id, tenant_id=t)
    imgs = [im for im in db.list_images(conn, att_id, tenant_id=t) if im["selected"]]
    conn.close()
    body_html = md_to_html(row["content"]) if row and row["content"] else ""
    return templates.TemplateResponse(
        request, "article.html",
        {"it": row, "body_html": body_html, "images": imgs, "label": STATUS_LABEL})


@app.post("/item/{att_id}/generate")
def generate(request: Request, att_id: int):
    t = _tenant(request)
    conn = db.connect()
    if not subscription.can_use(conn, t)[0]:
        conn.close()
        return RedirectResponse(f"/item/{att_id}", status_code=303)
    row = db.get_item(conn, att_id, tenant_id=t)
    if row and row["extracted_text"]:
        a = generate_article(row["extracted_text"], source_title=row["filename"])
        db.upsert_draft(conn, att_id, a.headline, render_markdown(a), "draft", tenant_id=t)
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


@app.post("/item/{att_id}/save")
def save(request: Request, att_id: int, headline: str = Form(...), content: str = Form(...)):
    conn = db.connect()
    db.upsert_draft(conn, att_id, headline, content, "draft", tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


def _publish_one(conn, att_id: int, tenant_id: int) -> bool:
    row = db.get_item(conn, att_id, tenant_id=tenant_id)
    if not row or not row["content"]:
        return False
    imgs = [{"path": im["path"], "caption": im["caption"]}
            for im in db.list_images(conn, att_id, tenant_id=tenant_id) if im["selected"]]
    cfg = db.get_tenant_config(conn, tenant_id) or {}
    result = get_publisher(cfg).publish(att_id, row["headline"] or "", row["content"], imgs)
    if result.ok:
        db.mark_published(conn, att_id, result.url, tenant_id=tenant_id)
    return result.ok


@app.post("/item/{att_id}/status")
def status(request: Request, att_id: int, status: str = Form(...)):
    t = _tenant(request)
    conn = db.connect()
    if status == "published":
        _publish_one(conn, att_id, t)
    else:
        db.set_draft_status(conn, att_id, status, tenant_id=t)
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


@app.post("/bulk")
def bulk(request: Request, action: str = Form(...), ids: Annotated[list[int], Form()] = []):
    t = _tenant(request)
    conn = db.connect()
    if action == "generate":
        if not subscription.can_use(conn, t)[0]:
            conn.close()
            return RedirectResponse("/", status_code=303)
        for att_id in ids:
            row = db.get_item(conn, att_id, tenant_id=t)
            if row and row["extracted_text"] and not row["content"]:
                a = generate_article(row["extracted_text"], source_title=row["filename"])
                db.upsert_draft(conn, att_id, a.headline, render_markdown(a), "draft", tenant_id=t)
    elif action == "publish":
        for att_id in ids:
            _publish_one(conn, att_id, t)
    elif action == "review":
        db.bulk_set_status(conn, ids, "reviewed", tenant_id=t)
    conn.close()
    return RedirectResponse("/", status_code=303)
