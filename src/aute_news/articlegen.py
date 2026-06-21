"""기사 작성(Generate) 스테이지 (이식명세 §4) — prompt_Generate.txt 사용.

Split 로 나뉜 기사(article.body)를 입력받아 제목·부제·본문HTML·카테고리·
source_info·editor_notes 를 생성하고 articles 행을 채운다(status→drafted).
홍보성 메타블록·URL 은 프롬프트가 보존하지만, 결정론적 백업도 적용(§4-3).
"""
from __future__ import annotations

import datetime
import html as _html
import json
import re

from . import db
from .config import normalize_category
from .llm import get_llm, load_prompt

_META_HDR = re.compile(r"^[\s]*[■□▶◆▪◇★◎●]\s*\S")
_META_ITEM = re.compile(r"^[\s]*[-•⦁·∙]\s*[^:：]{1,30}\s*[:：]")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_SKIP_URL = ("googleusercontent", "ci3.google", "kmmailer")


def _extract_meta_blocks(text: str) -> list[dict]:
    blocks, cur = [], None
    for raw in (text or "").splitlines():
        t = raw.strip()
        if _META_HDR.match(t):
            if cur and len(cur["items"]) >= 2:
                blocks.append(cur)
            cur = {"title": re.sub(r"^[■□▶◆▪◇★◎●]\s*", "", t), "items": []}
        elif cur and _META_ITEM.match(t):
            cur["items"].append(re.sub(r"^[-•⦁·∙]\s*", "", t))
    if cur and len(cur["items"]) >= 2:
        blocks.append(cur)
    return blocks


def promo_postprocess(body_html: str, source_text: str, article_type: str) -> str:
    """홍보성인데 본문에 빠진 메타블록·URL 을 결정론적으로 보존(이식명세 §4-3)."""
    if article_type != "홍보성":
        return body_html
    add = ""
    for b in _extract_meta_blocks(source_text):
        if b["items"] and b["items"][0][:12] not in body_html:
            items = "<br>".join(f"- {_html.escape(i)}" for i in b["items"])
            add += f"<p><strong>□ {_html.escape(b['title'])}</strong></p><p>{items}</p>"
    urls = [u for u in dict.fromkeys(_URL_RE.findall(source_text or ""))
            if not any(s in u.lower() for s in _SKIP_URL) and u not in body_html]
    if urls:
        add += "<p>" + "<br>".join(f'<a href="{u}">{_html.escape(u)}</a>' for u in urls) + "</p>"
    if add:
        body_html += ("<p><strong>※ 자세한 내용·문의·참가 신청은 아래 안내를 "
                      "참고해 주세요.</strong></p>" + add)
    return body_html


def generate(body_text: str, *, email_subject: str = "", email_from: str = "",
             email_date: str = "", images_meta: list[dict] | None = None) -> dict:
    """원문 텍스트 → Generate 스키마 dict."""
    system, _ = load_prompt("Generate")
    today = datetime.date.today()
    images_meta = images_meta or []
    img_lines = ("\n".join(f"  · {i.get('fileName','')} (출처: {i.get('source','unknown')})"
                           for i in images_meta) or "  (없음)")
    user = f"""## 기준 정보
- 오늘 날짜: {today.isoformat()}
- 요일: {today.strftime('%A')}

## 보도자료 내용
- 제목: {email_subject}
- 수신 일시 (참고용 — 시제 판단 기준 아님): {email_date}
- 보낸곳: {email_from}

- 본문 및 첨부파일 내용:
{body_text}

## 첨부 이미지 정보
- 이미지 개수: {len(images_meta)}
- 이미지 목록:
{img_lines}"""
    return get_llm().complete_json(system, user, temperature=0.3)


def generate_for_article(conn, article_id: int, tenant_id: int = db.DEFAULT_TENANT) -> dict:
    """articles 행 1건 기사화 후 DB 갱신."""
    art = db.get_article(conn, article_id, tenant_id=tenant_id)
    if not art:
        raise ValueError(f"article {article_id} 없음")
    # 출처 메일 메타
    subject = sender = date = ""
    if art["attachment_id"]:
        row = conn.execute(
            """SELECT m.subject, m.sender, m.date FROM attachments a
               JOIN messages m ON m.id=a.message_pk WHERE a.id=? AND a.tenant_id=?""",
            (art["attachment_id"], tenant_id)).fetchone()
        if row:
            subject, sender, date = row["subject"], row["sender"], row["date"]
    imgs = db.list_article_images(conn, article_id, tenant_id=tenant_id)
    images_meta = [{"fileName": __import__("os").path.basename(i["path"] or ""),
                    "source": "email_attachment"} for i in imgs]

    res = generate(art["body"] or "", email_subject=subject or art["title"] or "",
                   email_from=sender or "", email_date=date or "", images_meta=images_meta)

    body_html = promo_postprocess(res.get("article_body_html", ""),
                                  art["body"] or "", res.get("article_type", ""))
    db.update_article_generated(
        conn, article_id,
        headline=res.get("article_title", art["title"] or ""),
        subtitle=res.get("article_subtitle", ""),
        content_html=body_html,
        category_code=normalize_category(res.get("category_code")),
        article_type=res.get("article_type", ""),
        source_info=json.dumps(res.get("source_info", {}), ensure_ascii=False),
        editor_notes=json.dumps(res.get("editor_notes", {}), ensure_ascii=False),
        tenant_id=tenant_id)
    return res
