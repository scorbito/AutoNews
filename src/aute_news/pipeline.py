"""오케스트레이션 (이식명세 §0,§9) — 메일 1통을 끝까지 처리.

  triage → route → (download/body) → extract → split → image match → generate
        → 모드 분기 (review: 초안에서 멈춤 / auto: 즉시 발행)

발행 모드: PIPELINE_MODE = review(기본) | auto
SKIP/NEEDS_REVIEW 는 처리하지 않고 상태만 남긴다(기자 UI 에서 확인).
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from . import articlegen, db, images, links
from .extractors import extract_url, select_primary
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
    imgs = [{"path": i["path"], "caption": i["caption"] or "", "source": i["source"] or ""}
            for i in db.list_article_images(conn, article_id, tenant_id=tenant_id)]
    cfg = db.get_tenant_config(conn, tenant_id) or {}
    res = get_publisher(cfg).publish(
        article_id, art["headline"] or "", art["content_html"], imgs,
        category=art["category_code"], subtitle=art["subtitle"] or "", body_is_html=True)
    if res and res.ok:
        db.mark_article_published(conn, article_id, res.url, tenant_id=tenant_id)
    return res


def _split_and_generate(conn, attachment_id: int, text: str, subject: str, sender: str,
                        mode: str, tenant_id: int = db.DEFAULT_TENANT,
                        message_pk: int | None = None) -> list[int]:
    """추출 텍스트 → split → 이미지매칭 → generate → (auto면)발행. 기사 id 목록 반환."""
    res = run_split(text, subject=subject,
                    from_name=(sender or "").split("<")[0].strip(),
                    body_text_preview="")
    arts = res.get("articles", []) or []
    db.clear_articles(conn, attachment_id, tenant_id=tenant_id)
    ids = []
    for a in arts:
        aid = db.insert_article(
            conn, tenant_id=tenant_id, attachment_id=attachment_id,
            sequence_number=a.get("sequence_number", 1),
            title=a.get("title", ""), body=a.get("body", ""),
            contact_info=json.dumps(a.get("contact_info"), ensure_ascii=False),
            category_hint=a.get("category_hint"), status="split")
        ids.append(aid)
    conn.commit()
    # 이미지 매칭(독립 단계): 메일 전체 이미지(zip+임베드)를 기사에 배정.
    if message_pk is not None:
        images.match_message_images(conn, message_pk, attachment_id, tenant_id=tenant_id)
    else:
        images.match_images_to_articles(conn, attachment_id, tenant_id=tenant_id)
    for aid in ids:
        articlegen.generate_for_article(conn, aid, tenant_id=tenant_id)
        if mode == "auto":
            publish_article(conn, aid, tenant_id=tenant_id)
    return ids


def _generate_link_articles(conn, message_pk: int, urls: list[str], sender: str,
                            mode: str, tenant_id: int = db.DEFAULT_TENANT) -> list[int]:
    """본문 기사 링크 → 링크당 기사 1건. 가져온 이미지엔 출처(URL) 기록."""
    ids = []
    for url in urls:
        try:
            draft = extract_url(url)
        except Exception:  # noqa: BLE001 (fetch/추출 실패는 건너뜀)
            continue
        if len((draft.body_text or "").strip()) < 100:
            continue
        pk = db.insert_attachment(
            conn, tenant_id=tenant_id, message_pk=message_pk,
            filename=(draft.title or url)[:200], format="weblink", path=url,
            size=len(draft.body_text or ""), extracted_text=draft.body_text,
            extract_status="done")
        conn.commit()
        if draft.images:
            src_name = draft.source_name or urlparse(url).netloc.replace("www.", "")
            images.process_images(conn, pk, draft, tenant_id=tenant_id, source=src_name)
        aid = db.insert_article(
            conn, tenant_id=tenant_id, attachment_id=pk, sequence_number=1,
            title=draft.title or "", body=draft.body_text or "",
            contact_info=None, category_hint=None, status="split")
        conn.commit()
        # 링크 기사 = 단건 → 그 링크의 이미지만 그 기사에 배정(첨부 단위)
        images.match_images_to_articles(conn, pk, tenant_id=tenant_id)
        articlegen.generate_for_article(conn, aid, tenant_id=tenant_id)
        if mode == "auto":
            publish_article(conn, aid, tenant_id=tenant_id)
        ids.append(aid)
    return ids


def process_message(conn, message_pk: int, mode: str | None = None,
                    tenant_id: int = db.DEFAULT_TENANT) -> dict:
    mode = mode or _mode()
    msg = conn.execute("SELECT * FROM messages WHERE id=? AND tenant_id=?",
                       (message_pk, tenant_id)).fetchone()
    if not msg:
        return {"error": "메일 없음"}
    atts = db.message_attachments(conn, message_pk, tenant_id=tenant_id)

    # 1) Triage (없으면 수행)
    pipeline = msg["pipeline"]
    if not pipeline:
        meta = build_meta(msg["subject"], msg["sender"], msg["body_text"], atts)
        tri = run_triage(meta)
        pipeline = tri["pipeline"]
        db.set_triage(conn, message_pk, pipeline, tri.get("triage_confidence"),
                      tri.get("reasoning", ""), tenant_id=tenant_id)

    result = {"message_pk": message_pk, "pipeline": pipeline, "mode": mode, "articles": []}

    if pipeline in ("SKIP", "NEEDS_REVIEW"):
        result["skipped"] = True
        return result

    # 재처리 시 이전 합성 첨부(weblink/body) 정리 → 링크 기사 중복 누적 방지
    db.clear_synthetic_attachments(conn, message_pk, tenant_id=tenant_id)
    conn.commit()

    body = msg["body_text"] or ""
    # 본문 기사 링크(있으면) — 첨부와 별개로 링크당 1기사. 다이제스트 본문 판별에도 사용.
    cands = links.extract_link_candidates(body)
    link_urls = links.pick_article_urls(cands) if cands else []

    ids: list[int] = []

    # 2) 본문/첨부 기사화
    att_rows = conn.execute(
        "SELECT * FROM attachments WHERE message_pk=? AND tenant_id=? AND extract_status='done'",
        (message_pk, tenant_id)).fetchall()

    if pipeline == "BODY_AS_ARTICLE" or not att_rows:
        # 본문이 진짜 기사일 때만 본문 기사화(링크 다이제스트면 링크로만 처리)
        if not link_urls and len(body.strip()) >= 50:
            pk = db.insert_attachment(conn, tenant_id=tenant_id, message_pk=message_pk,
                                      filename="(본문)", format="body", path="", size=len(body),
                                      extracted_text=body, extract_status="done")
            conn.commit()
            ids += _split_and_generate(conn, pk, body, msg["subject"], msg["sender"],
                                       mode, tenant_id, message_pk=message_pk)
    else:
        # 첨부 기반: 본문 추출용 1건 선택
        primary = select_primary([{"filename": r["filename"]} for r in att_rows])
        chosen = att_rows[0]
        if primary:
            chosen = next((r for r in att_rows if r["filename"] == primary["filename"]), att_rows[0])
        ids += _split_and_generate(conn, chosen["id"], chosen["extracted_text"],
                                   msg["subject"], msg["sender"], mode, tenant_id,
                                   message_pk=message_pk)

    # 3) 본문 링크 기사화 (첨부와 병행)
    if link_urls:
        ids += _generate_link_articles(conn, message_pk, link_urls, msg["sender"], mode, tenant_id)

    if not ids:
        result["skipped"] = "기사화할 내용 없음"
    result["articles"] = ids
    return result
