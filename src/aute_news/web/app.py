"""기자 검토 UI (FastAPI + Jinja2) — Supabase Auth 로그인 + 멀티테넌트.

로그인한 신문사(tenant)의 데이터만 보이고 처리된다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import admin, articlegen, auth, db
from ..config import CATEGORY_CODES
from ..generator import generate_article, render_markdown
from ..pipeline import publish_article
from ..publishers import get_publisher
from ..storage import get_storage, mime_for

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="aute_news 기자 검토")

STATUS_LABEL = {None: "미생성", "draft": "초안", "reviewed": "검토완료", "published": "발행됨"}
ASTATUS = {None: "-", "split": "분할됨", "drafted": "초안",
           "reviewed": "검토완료", "published": "발행됨"}
AFILTERS = [("all", "전체"), ("drafted", "초안"), ("reviewed", "검토완료"),
            ("published", "발행됨"), ("split", "분할만")]
FILTERS = [("all", "전체"), ("none", "미생성"), ("draft", "초안"),
           ("reviewed", "검토완료"), ("published", "발행됨")]

_PUBLIC_PATHS = ("/login", "/logout")


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
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


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


@app.post("/admin/tenant/{tid}/user")
def admin_add_user(request: Request, tid: int, email: str = Form(...), password: str = Form(...)):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    conn = db.connect()
    try:
        admin.add_user(conn, tid, email, password)
        msg = f"기자 추가: {email} → 신문사 {tid}"
    except Exception as e:  # noqa: BLE001
        msg = f"실패: {e}"
    conn.close()
    return RedirectResponse(f"/admin?msg={msg}", status_code=303)


@app.post("/admin/config/{tid}")
async def admin_config(request: Request, tid: int):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    form = await request.form()
    kw = {k: v for k, v in form.items() if v not in (None, "")}
    if "collect_enabled" in kw:
        kw["collect_enabled"] = int(kw["collect_enabled"])  # INTEGER 컬럼
    conn = db.connect()
    db.set_tenant_config(conn, tid, **kw)
    conn.close()
    return RedirectResponse(f"/admin?msg=테넌트 {tid} 설정 저장", status_code=303)


@app.post("/admin/collect/{tid}")
def admin_collect(request: Request, tid: int):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    stats = admin.collect_tenant(tid)
    return RedirectResponse(f"/admin?msg=수집: {stats}", status_code=303)


@app.post("/admin/process/{tid}")
def admin_process(request: Request, tid: int):
    if not _require_admin(request):
        return HTMLResponse("관리자 전용입니다.", 403)
    conn = db.connect()
    made = admin.process_tenant(conn, tid)
    conn.close()
    return RedirectResponse(f"/admin?msg=처리 완료: 기사 {made}건 생성", status_code=303)


# ── 기사(articles) ────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "all"):
    t = _tenant(request)
    conn = db.connect()
    arts = db.list_all_articles(conn, status, tenant_id=t)
    counts = db.article_status_counts(conn, tenant_id=t)
    conn.close()
    return templates.TemplateResponse(
        request, "articles.html",
        {"arts": arts, "astatus": ASTATUS, "filters": AFILTERS, "counts": counts,
         "active": status, "cats": CATEGORY_CODES, "email": request.session.get("email")})


@app.post("/collect")
def collect_now(request: Request):
    """기자가 본인 신문사 메일을 지금 수집."""
    from ..collector import collect_for_tenant
    try:
        stats = collect_for_tenant(_tenant(request))
    except Exception as e:  # noqa: BLE001 (메일 로그인 실패 등)
        return RedirectResponse(f"/legacy?msg=수집 실패: {type(e).__name__}", status_code=303)
    if stats.get("skipped"):
        msg = f"수집 불가: {stats['skipped']} (관리자에게 메일 설정 요청)"
    else:
        msg = (f"수집 완료 — 새 메일 {stats.get('new_messages', 0)}건, "
               f"첨부 {stats.get('attachments', 0)}개")
    return RedirectResponse(f"/legacy?msg={msg}", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request):
    conn = db.connect()
    cfg = db.get_tenant_config(conn, _tenant(request)) or {}
    conn.close()
    auto_on = bool(cfg.get("collect_enabled")) and (cfg.get("pipeline_mode") == "auto")
    return templates.TemplateResponse(
        request, "settings.html",
        {"auto_on": auto_on, "collect_times": cfg.get("collect_times") or "",
         "has_mail": bool(cfg.get("imap_host"))})


@app.post("/settings")
def settings_save(request: Request, auto_mode: str = Form("0"), collect_times: str = Form("")):
    on = auto_mode == "1"
    conn = db.connect()
    db.set_tenant_config(conn, _tenant(request),
                         collect_enabled=1 if on else 0,
                         pipeline_mode="auto" if on else "review",
                         collect_times=collect_times.strip())
    conn.close()
    return RedirectResponse("/settings", status_code=303)


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


@app.post("/a/{article_id}/review")
def article_review(request: Request, article_id: int):
    conn = db.connect()
    db.set_article_status(conn, article_id, "reviewed", tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.post("/a/{article_id}/publish")
def article_publish(request: Request, article_id: int):
    conn = db.connect()
    publish_article(conn, article_id, tenant_id=_tenant(request))
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


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
