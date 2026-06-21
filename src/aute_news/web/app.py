"""기자 검토 UI (FastAPI + Jinja2).

화면 흐름:
  목록(/)  →  상세(/item/{id})  →  [초안 생성] / [편집 저장] / [상태 변경]
상태: collected → (초안생성) draft → (검토) reviewed → (발행) published
"""
from __future__ import annotations

from pathlib import Path

from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import json

from .. import articlegen, db
from ..generator import generate_article, render_markdown
from ..pipeline import publish_article
from ..publishers import get_publisher

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="aute_news 기자 검토")

STATUS_LABEL = {
    None: "미생성", "draft": "초안", "reviewed": "검토완료", "published": "발행됨",
}
# 기사(articles) 상태 라벨
ASTATUS = {None: "-", "split": "분할됨", "drafted": "초안",
           "reviewed": "검토완료", "published": "발행됨"}
AFILTERS = [("all", "전체"), ("drafted", "초안"), ("reviewed", "검토완료"),
            ("published", "발행됨"), ("split", "분할만")]


def _jload(s):
    try:
        return json.loads(s) if s else {}
    except (ValueError, TypeError):
        return {}


FILTERS = [("all", "전체"), ("none", "미생성"), ("draft", "초안"),
           ("reviewed", "검토완료"), ("published", "발행됨")]


def md_to_html(text: str) -> str:
    """저장된 마크다운 기사를 간단히 HTML 로 렌더(읽기 전용 보기용)."""
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


from ..config import CATEGORY_CODES  # noqa: E402


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "all"):
    """기사(articles) 목록 — 파이프라인 산출물(검토/발행 단위)."""
    conn = db.connect()
    arts = db.list_all_articles(conn, status)
    counts = db.article_status_counts(conn)
    conn.close()
    return templates.TemplateResponse(
        request, "articles.html",
        {"arts": arts, "astatus": ASTATUS, "filters": AFILTERS,
         "counts": counts, "active": status, "cats": CATEGORY_CODES})


@app.get("/a/{article_id}", response_class=HTMLResponse)
def article_detail(request: Request, article_id: int):
    conn = db.connect()
    art = db.get_article(conn, article_id)
    imgs = db.list_article_images(conn, article_id)
    conn.close()
    return templates.TemplateResponse(
        request, "article_detail.html",
        {"a": art, "images": imgs, "astatus": ASTATUS, "cats": CATEGORY_CODES,
         "source_info": _jload(art["source_info"]) if art else {},
         "editor_notes": _jload(art["editor_notes"]) if art else {}})


@app.post("/a/{article_id}/save")
def article_save(article_id: int, headline: str = Form(...), subtitle: str = Form(""),
                 content_html: str = Form(...), category_code: str = Form("S1N10")):
    conn = db.connect()
    db.update_article_edit(conn, article_id, headline=headline, subtitle=subtitle,
                           content_html=content_html, category_code=category_code)
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.post("/a/{article_id}/review")
def article_review(article_id: int):
    conn = db.connect()
    db.set_article_status(conn, article_id, "reviewed")
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.post("/a/{article_id}/publish")
def article_publish(article_id: int):
    conn = db.connect()
    publish_article(conn, article_id)   # 활성 발행기(기본 HTML, atpaju는 하드잠금)
    conn.close()
    return RedirectResponse(f"/a/{article_id}", status_code=303)


@app.get("/legacy", response_class=HTMLResponse)
def index_legacy(request: Request, status: str = "all"):
    conn = db.connect()
    items = db.list_items(conn, None if status == "all" else status)
    counts = db.status_counts(conn)
    conn.close()
    return templates.TemplateResponse(
        request, "list.html",
        {"items": items, "label": STATUS_LABEL, "filters": FILTERS,
         "counts": counts, "active": status})


@app.get("/item/{att_id}", response_class=HTMLResponse)
def item(request: Request, att_id: int):
    conn = db.connect()
    row = db.get_item(conn, att_id)
    imgs = db.list_images(conn, att_id)
    conn.close()
    return templates.TemplateResponse(
        request, "detail.html", {"it": row, "images": imgs, "label": STATUS_LABEL})


@app.get("/img/{image_id}")
def serve_img(image_id: int):
    conn = db.connect()
    r = conn.execute("SELECT path FROM images WHERE id=?", (image_id,)).fetchone()
    conn.close()
    return FileResponse(r["path"]) if r and r["path"] else HTMLResponse("not found", 404)


@app.post("/image/{image_id}/toggle")
def toggle_img(image_id: int, att_id: int = Form(...), selected: int = Form(...)):
    conn = db.connect()
    db.set_image_selected(conn, image_id, bool(selected))
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


@app.get("/article/{att_id}", response_class=HTMLResponse)
def article(request: Request, att_id: int):
    """발행/완성 기사 읽기 전용 보기."""
    conn = db.connect()
    row = db.get_item(conn, att_id)
    imgs = [im for im in db.list_images(conn, att_id) if im["selected"]]
    conn.close()
    body_html = md_to_html(row["content"]) if row and row["content"] else ""
    return templates.TemplateResponse(
        request, "article.html",
        {"it": row, "body_html": body_html, "images": imgs, "label": STATUS_LABEL})


@app.post("/item/{att_id}/generate")
def generate(att_id: int):
    conn = db.connect()
    row = db.get_item(conn, att_id)
    if row and row["extracted_text"]:
        article = generate_article(row["extracted_text"], source_title=row["filename"])
        db.upsert_draft(conn, att_id, article.headline, render_markdown(article), "draft")
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


@app.post("/item/{att_id}/save")
def save(att_id: int, headline: str = Form(...), content: str = Form(...)):
    conn = db.connect()
    db.upsert_draft(conn, att_id, headline, content, "draft")
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


def _publish_one(conn, att_id: int) -> bool:
    """초안이 있는 항목을 발행 어댑터로 발행하고 결과를 저장."""
    row = db.get_item(conn, att_id)
    if not row or not row["content"]:
        return False
    imgs = [{"path": im["path"], "caption": im["caption"]}
            for im in db.list_images(conn, att_id) if im["selected"]]
    result = get_publisher().publish(att_id, row["headline"] or "", row["content"], imgs)
    if result.ok:
        db.mark_published(conn, att_id, result.url)
    return result.ok


@app.post("/item/{att_id}/status")
def status(att_id: int, status: str = Form(...)):
    conn = db.connect()
    if status == "published":
        _publish_one(conn, att_id)
    else:
        db.set_draft_status(conn, att_id, status)
    conn.close()
    return RedirectResponse(f"/item/{att_id}", status_code=303)


@app.post("/bulk")
def bulk(action: str = Form(...), ids: Annotated[list[int], Form()] = []):
    """목록에서 체크한 기사들을 일괄 처리(초안생성/검토완료/발행)."""
    conn = db.connect()
    if action == "generate":
        # 추출 텍스트가 있고 아직 초안이 없는 항목만 생성(중복 호출/비용 방지)
        for att_id in ids:
            row = db.get_item(conn, att_id)
            if row and row["extracted_text"] and not row["content"]:
                art = generate_article(row["extracted_text"], source_title=row["filename"])
                db.upsert_draft(conn, att_id, art.headline, render_markdown(art), "draft")
    elif action == "publish":
        for att_id in ids:
            _publish_one(conn, att_id)
    elif action == "review":
        db.bulk_set_status(conn, ids, "reviewed")
    conn.close()
    return RedirectResponse("/", status_code=303)
