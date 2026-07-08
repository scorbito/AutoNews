"""기사 작성(Generate) 스테이지 (이식명세 §4) — prompt_Generate.txt 사용.

Split 로 나뉜 기사(article.body)를 입력받아 제목·부제·본문HTML·카테고리·
source_info·editor_notes 를 생성하고 articles 행을 채운다(status→drafted).
홍보성 메타블록·URL 은 프롬프트가 보존하지만, 결정론적 백업도 적용(§4-3).
"""
from __future__ import annotations

import datetime
import html as _html
import json
import os
import re

from . import db
from .config import normalize_category
from .llm import get_llm, load_prompt

# 기사 생성은 품질 우선 — 기본 flash(나머지 단계는 LLM_MODEL=flash-lite).
GENERATE_MODEL = "gemini-2.5-flash"

# 기사 스타일 프리셋 — 톤·길이·구조만 바꾼다(사실 정확성은 시스템 프롬프트가 최우선).
STYLE_PROMPTS = {
    "standard": "",   # 표준 스트레이트(기본) — 추가 지시 없음
    "deep": "상세·심층형: 자료에 있는 배경·맥락·의미를 충분히 풀어 길고 깊게 쓴다. "
            "단 자료에 없는 사실·수치·발언은 절대 만들지 않는다.",
    "brief": "단신형: 핵심만 3~4문장으로 아주 짧게. 부연·수식어를 최소화한다.",
    "local": "지역 밀착·친근형: 딱딱한 관공서 문투를 줄이고 주민이 읽기 쉬운 자연스러운 문장으로 쓴다. "
             "단 사실·수치·기관명은 정확히 유지한다.",
    "quote": "인용 강조형: 관계자·담당자의 발언(직접 인용문)을 중심으로 구성한다. "
             "자료에 인용문이 없으면 지어내지 말고 사실 전달 문장으로 대체한다.",
}


def _style_instruction(style_key: str | None, custom_text: str = "") -> str:
    """설정된 스타일 → 프롬프트에 넣을 지시 문자열(없으면 '')."""
    if (style_key or "") == "custom" and (custom_text or "").strip():
        return ("사용자 지정 문체 취향: " + custom_text.strip()[:500] +
                " — 단, 이 문체는 톤·표현에만 적용하고 자료에 없는 사실을 지어내거나 왜곡하지 말 것. "
                "사실 정확성이 최우선이다.")
    return STYLE_PROMPTS.get(style_key or "standard", "")

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
             email_date: str = "", images_meta: list[dict] | None = None,
             style: str = "") -> dict:
    """원문 텍스트 → Generate 스키마 dict. style: 문체 지시(선택)."""
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
    if style:
        user += ("\n\n## 작성 스타일 지시 (톤·문체만; 사실은 위 규칙대로 정확히)\n" + style)
    model = os.getenv("GENERATE_MODEL", GENERATE_MODEL)
    return get_llm(model).complete_json(system, user, temperature=0.3)


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

    cfg = db.get_tenant_config(conn, tenant_id) or {}
    style = _style_instruction(cfg.get("article_style"), cfg.get("article_style_custom") or "")
    res = generate(art["body"] or "", email_subject=subject or art["title"] or "",
                   email_from=sender or "", email_date=date or "", images_meta=images_meta,
                   style=style)

    body_html = promo_postprocess(res.get("article_body_html", ""),
                                  art["body"] or "", res.get("article_type", ""))
    orig_title = res.get("article_title", art["title"] or "")
    orig_subtitle = res.get("article_subtitle", "")
    # 원본(기사체) 제목·부제를 SEO 목록 맨 앞에 함께 저장 → 언제든 되돌리기 가능
    seo_titles = ([{"label": "원본", "text": orig_title}] if orig_title else []) \
        + res.get("seo_titles", [])
    seo_subtitles = ([{"label": "원본", "text": orig_subtitle}] if orig_subtitle else []) \
        + res.get("seo_subtitles", [])
    db.update_article_generated(
        conn, article_id,
        headline=orig_title,
        subtitle=orig_subtitle,
        content_html=body_html,
        category_code=normalize_category(res.get("category_code")),
        article_type=res.get("article_type", ""),
        source_info=json.dumps(res.get("source_info", {}), ensure_ascii=False),
        editor_notes=json.dumps(res.get("editor_notes", {}), ensure_ascii=False),
        seo_suggestions=json.dumps(
            {"titles": seo_titles, "subtitles": seo_subtitles}, ensure_ascii=False),
        tenant_id=tenant_id)
    return res
