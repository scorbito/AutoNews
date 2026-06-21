"""오케스트레이션 (이식명세 §0,§9) — 메일 1통을 끝까지 처리.

  triage → route → (download/body) → extract → split → image match → generate
        → 모드 분기 (review: 초안에서 멈춤 / auto: 즉시 발행)

발행 모드: PIPELINE_MODE = review(기본) | auto
SKIP/NEEDS_REVIEW 는 처리하지 않고 상태만 남긴다(기자 UI 에서 확인).
"""
from __future__ import annotations

import json
import os

from . import articlegen, db, images
from .extractors import ExtractError, detect_format, extract_file, select_primary
from .publishers import get_publisher
from .split import run_split
from .triage import build_meta, run_triage


def _mode() -> str:
    return os.getenv("PIPELINE_MODE", "review").lower()


def publish_article(conn, article_id: int, tenant_id: int = db.DEFAULT_TENANT):
    """기사 1건을 활성 발행기로 발행하고 결과 저장."""
    art = db.get_article(conn, article_id, tenant_id=tenant_id)
    if not art or not art["content_html"]:
        return None
    imgs = [{"path": i["path"], "caption": i["caption"] or ""}
            for i in db.list_article_images(conn, article_id, tenant_id=tenant_id)]
    res = get_publisher().publish(
        article_id, art["headline"] or "", art["content_html"], imgs,
        category=art["category_code"], subtitle=art["subtitle"] or "", body_is_html=True)
    if res and res.ok:
        db.mark_article_published(conn, article_id, res.url, tenant_id=tenant_id)
    return res


def _split_and_generate(conn, attachment_id: int, text: str,
                        subject: str, sender: str, mode: str) -> list[int]:
    """추출 텍스트 → split → 이미지매칭 → generate → (auto면)발행. 기사 id 목록 반환."""
    res = run_split(text, subject=subject,
                    from_name=(sender or "").split("<")[0].strip(),
                    body_text_preview="")
    arts = res.get("articles", []) or []
    db.clear_articles(conn, attachment_id)
    ids = []
    for a in arts:
        aid = db.insert_article(
            conn, attachment_id=attachment_id, sequence_number=a.get("sequence_number", 1),
            title=a.get("title", ""), body=a.get("body", ""),
            contact_info=json.dumps(a.get("contact_info"), ensure_ascii=False),
            category_hint=a.get("category_hint"), status="split")
        ids.append(aid)
    conn.commit()
    images.match_images_to_articles(conn, attachment_id)
    for aid in ids:
        articlegen.generate_for_article(conn, aid)
        if mode == "auto":
            publish_article(conn, aid)
    return ids


def process_message(conn, message_pk: int, mode: str | None = None) -> dict:
    mode = mode or _mode()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (message_pk,)).fetchone()
    if not msg:
        return {"error": "메일 없음"}
    atts = db.message_attachments(conn, message_pk)

    # 1) Triage (없으면 수행)
    pipeline = msg["pipeline"]
    if not pipeline:
        meta = build_meta(msg["subject"], msg["sender"], msg["body_text"], atts)
        tri = run_triage(meta)
        pipeline = tri["pipeline"]
        db.set_triage(conn, message_pk, pipeline, tri.get("triage_confidence"),
                      tri.get("reasoning", ""))

    result = {"message_pk": message_pk, "pipeline": pipeline, "mode": mode, "articles": []}

    if pipeline in ("SKIP", "NEEDS_REVIEW"):
        result["skipped"] = True
        return result

    # 2) 본문 소스 확보
    att_rows = conn.execute(
        "SELECT * FROM attachments WHERE message_pk=? AND extract_status='done'",
        (message_pk,)).fetchall()

    if pipeline == "BODY_AS_ARTICLE" or not att_rows:
        # 메일 본문 자체를 보도자료로 → 합성(body) 첨부 1건
        body = msg["body_text"] or ""
        if len(body.strip()) < 50:
            result["skipped"] = "본문 부족"
            return result
        pk = db.insert_attachment(conn, message_pk=message_pk, filename="(본문)",
                                  format="body", path="", size=len(body),
                                  extracted_text=body, extract_status="done")
        conn.commit()
        result["articles"] = _split_and_generate(conn, pk, body, msg["subject"], msg["sender"], mode)
        return result

    # 3) 첨부 기반: 본문 추출용 1건 선택
    primary = select_primary([{"filename": r["filename"]} for r in att_rows])
    chosen = att_rows[0]
    if primary:
        chosen = next((r for r in att_rows if r["filename"] == primary["filename"]), att_rows[0])
    result["articles"] = _split_and_generate(
        conn, chosen["id"], chosen["extracted_text"], msg["subject"], msg["sender"], mode)
    return result
