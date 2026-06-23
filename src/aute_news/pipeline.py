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

from . import articlegen, db, images, links, router
from .extractors import ExtractError, detect_format, extract_bytes, extract_url
from .extractors.archive import IMAGE_EXTS
from .extractors.base import ImageAsset
from .publishers import get_publisher
from .split import run_split
from .storage import get_storage, mime_for

_DOC_FORMATS = ("hwp", "hwpx", "pdf", "docx", "doc")


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in (s or ""))[:80]


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


def _split_articles(conn, attachment_id: int, text: str, subject: str, sender: str,
                    tenant_id: int = db.DEFAULT_TENANT) -> list[int]:
    """추출 텍스트 → split → articles 행 삽입(status=split). 매칭·생성은 별도. id 목록 반환."""
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
    return ids


def _generate_all(conn, ids: list[int], mode: str, tenant_id: int) -> None:
    for aid in ids:
        articlegen.generate_for_article(conn, aid, tenant_id=tenant_id)
        if mode == "auto":
            publish_article(conn, aid, tenant_id=tenant_id)


def _split_and_generate(conn, attachment_id: int, text: str, subject: str, sender: str,
                        mode: str, tenant_id: int = db.DEFAULT_TENANT,
                        message_pk: int | None = None) -> list[int]:
    """단일 문서: split → 이미지매칭 → generate. 기사 id 목록 반환."""
    ids = _split_articles(conn, attachment_id, text, subject, sender, tenant_id)
    # 이미지 매칭(독립 단계): 메일 전체 이미지(zip+임베드)를 기사에 배정.
    if message_pk is not None:
        images.match_message_images(conn, message_pk, attachment_id, tenant_id=tenant_id)
    else:
        images.match_images_to_articles(conn, attachment_id, tenant_id=tenant_id)
    _generate_all(conn, ids, mode, tenant_id)
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


def _generate_download_articles(conn, message_pk: int, urls: list[str], mode: str,
                                tenant_id: int = db.DEFAULT_TENANT) -> list[int]:
    """본문의 파일 다운로드 링크 → 다운로드 후 문서별 기사화.

    순서 기반 그룹핑: 문서(hwp/pdf/…)가 그룹을 시작하고, 이어지는 이미지는 그 문서에 속함.
    (정부 보도자료 배포: 보도자료당 본문 1 + 참고이미지 N 이 순서대로 나열됨)
    """
    files = []
    for url in urls:
        got = links.download_file(url)
        if got:
            files.append((got[0], got[1], url))
    groups, cur = [], None
    for fn, data, url in files:
        fmt = detect_format(fn)
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        if fmt in ("hwp", "hwpx", "pdf", "docx", "doc"):
            cur = {"fn": fn, "data": data, "url": url, "fmt": fmt, "imgs": []}
            groups.append(cur)
        elif ext in IMAGE_EXTS and cur is not None:
            cur["imgs"].append((fn, data, ext))

    ids = []
    for idx, g in enumerate(groups):
        try:
            draft = extract_bytes(g["data"], g["fn"])
        except ExtractError:
            continue
        # 외부 다운로드 이미지(참고자료/사진)를 문서 본문에 합쳐 함께 저장
        for ifn, idata, iext in g["imgs"]:
            draft.images.append(ImageAsset(data=idata, ext=iext, source_ref=ifn))
        key = f"attachments/{tenant_id}/dl/{message_pk}_{idx}_{_safe(g['fn'])}"
        get_storage().put(key, g["data"], mime_for(g["fmt"]))
        pk = db.insert_attachment(
            conn, tenant_id=tenant_id, message_pk=message_pk, filename=g["fn"],
            format=g["fmt"], path=key, size=len(g["data"]),
            extracted_text=draft.body_text, extract_status="done")
        conn.commit()
        if draft.images:
            images.process_images(conn, pk, draft, tenant_id=tenant_id)
        # 다운로드 문서 = 보도자료 1건 → split(보통 1) + 첨부단위 이미지 매칭(message_pk 미전달)
        ids += _split_and_generate(conn, pk, draft.body_text, g["fn"], "", mode, tenant_id)
    return ids


def process_message(conn, message_pk: int, mode: str | None = None,
                    tenant_id: int = db.DEFAULT_TENANT) -> dict:
    mode = mode or _mode()
    msg = conn.execute("SELECT * FROM messages WHERE id=? AND tenant_id=?",
                       (message_pk, tenant_id)).fetchone()
    if not msg:
        return {"error": "메일 없음"}
    atts = db.message_attachments(conn, message_pk, tenant_id=tenant_id)
    body = msg["body_text"] or ""

    # 1) LLM 라우터 — 메일 통째로 보고 처리 계획(스킵/본문기사여부/기사링크/다운로드링크).
    #    실패하면 코드 휴리스틱으로 폴백.
    cand = links.extract_link_candidates(body)
    plan = router.plan_email(msg["subject"], msg["sender"], body,
                             [a["filename"] for a in atts], cand)
    if plan is None:
        dl = links.extract_download_links(body)
        plan = {"skip": False, "body_is_article": True, "reason": "router 실패→휴리스틱",
                "download_links": dl,
                "article_links": links.pick_article_urls(
                    [c for c in cand if c["url"] not in set(dl)])}

    db.set_triage(conn, message_pk, "SKIP" if plan["skip"] else "ROUTED", None,
                  plan.get("reason", ""), tenant_id=tenant_id)
    result = {"message_pk": message_pk,
              "pipeline": "SKIP" if plan["skip"] else "ROUTED", "mode": mode, "articles": []}
    if plan["skip"]:
        result["skipped"] = True
        return result

    link_urls = plan["article_links"]
    download_urls = plan["download_links"]

    # 재처리 시 이전 합성 첨부(weblink/body) 정리 → 링크 기사 중복 누적 방지
    db.clear_synthetic_attachments(conn, message_pk, tenant_id=tenant_id)
    conn.commit()

    ids: list[int] = []

    # 2) 본문/첨부 기사화
    att_rows = conn.execute(
        "SELECT * FROM attachments WHERE message_pk=? AND tenant_id=? AND extract_status='done'",
        (message_pk, tenant_id)).fetchall()

    doc_atts = [r for r in att_rows
                if r["format"] in _DOC_FORMATS and (r["extracted_text"] or "").strip()]

    if len(doc_atts) == 1:
        # 단일 문서(첨부+ZIP 사진 등) — 검증된 단일 경로
        d = doc_atts[0]
        ids += _split_and_generate(conn, d["id"], d["extracted_text"], msg["subject"],
                                   msg["sender"], mode, tenant_id, message_pk=message_pk)
    elif len(doc_atts) > 1:
        # 문서 여러 개(메일에 보도자료 여러 건이 각각 첨부) — 각 문서 → 기사,
        # 메시지 전체 이미지를 전체 기사에 한 번에 매칭(LLM, 제목·파일명 기준)
        art_ids = []
        for d in doc_atts:
            art_ids += _split_articles(conn, d["id"], d["extracted_text"],
                                       msg["subject"], msg["sender"], tenant_id)
        images.match_message_images_all(conn, message_pk, tenant_id=tenant_id)
        _generate_all(conn, art_ids, mode, tenant_id)
        ids += art_ids
    elif (plan["body_is_article"] and not link_urls and not download_urls
          and len(body.strip()) >= 50):
        # 문서·링크·다운로드 없음 + 라우터가 '본문이 기사'로 판단 → 본문 자체를 기사화
        # (링크/다운로드가 있으면 본문은 표지/다이제스트로 보고 기사화 안 함)
        pk = db.insert_attachment(conn, tenant_id=tenant_id, message_pk=message_pk,
                                  filename="(본문)", format="body", path="", size=len(body),
                                  extracted_text=body, extract_status="done")
        conn.commit()
        ids += _split_and_generate(conn, pk, body, msg["subject"], msg["sender"],
                                   mode, tenant_id, message_pk=message_pk)

    # 3) 본문 링크 기사화 (첨부와 병행)
    if link_urls:
        ids += _generate_link_articles(conn, message_pk, link_urls, msg["sender"], mode, tenant_id)

    # 4) 본문 파일 다운로드 링크 기사화 (정부 보도자료 배포 등)
    if download_urls:
        ids += _generate_download_articles(conn, message_pk, download_urls, mode, tenant_id)

    # 5) 기사에 배정된 이미지 중 같은 사진은 1장만 (수집은 중복 허용, 기사엔 제외)
    images.dedup_article_images(conn, message_pk, tenant_id=tenant_id)

    if not ids:
        result["skipped"] = "기사화할 내용 없음"
    result["articles"] = ids
    return result
