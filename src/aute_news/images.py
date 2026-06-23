"""이미지 처리 — 추출된 이미지를 저장하고 '기사용 사진'만 선별.

선별 2단계:
  1) 크기 필터 — 한 변이 너무 작으면(아이콘/직인 추정) 비채택
  2) (API 키 있으면) Gemini 멀티모달 분류 — 사진 vs 직인/로고/서식
저장: data/images/{att_id}/{n}.{ext}  +  images 테이블
"""
from __future__ import annotations

import io
import re

from PIL import Image

from . import db
from .extractors.base import ArticleDraft
from .storage import get_storage, mime_for

# 한 변이 이 px 미만이면 아이콘/장식으로 보고 제외
_MIN_SIDE = 150
# 사진으로 자동 채택할 최소 한 변(이 이상이면 크기상 사진일 확률↑)
_PHOTO_SIDE = 500


def _seq_from_name(name: str | None) -> int | None:
    """파일명 선두 번호 추출 — '7-2.'→7, '사진3_'→3. 폴백 힌트용."""
    import os
    base = os.path.basename(name or "")
    m = re.search(r"(?:사진|photo)?\s*(\d+)[-_.]", base) or re.match(r"\s*(\d+)", base)
    return int(m.group(1)) if m else None


def _img_name(im) -> str:
    import os
    return im["orig_name"] or os.path.basename(im["path"] or "")


def _llm_assign(articles: list, imgs: list) -> dict:
    """LLM(기본 flash-lite)에게 '기사 제목 ↔ 사진 파일명' 매칭을 맡긴다.

    반환 {image_id: article_seq | None}. 전체를 한 번에 보고 배정(전역 시야).
    """
    from .llm import get_llm
    art_lines = "\n".join(
        f"  {a['sequence_number']}. {a['headline'] or a['title'] or ''}" for a in articles)
    img_lines = "\n".join(f"  [{im['id']}] {_img_name(im)}" for im in imgs)
    system = (
        "너는 보도자료 사진을 기사에 배정하는 편집 보조다. "
        "기사 목록(번호. 제목)과 사진 목록([id] 파일명)이 주어진다. "
        "각 사진을 가장 알맞은 기사 '번호'(article_seq)에 배정하라. "
        "파일명 앞 숫자(예 '7-2.'는 7번)와 파일명에 담긴 제목을 단서로 쓴다. "
        "어느 기사에도 맞지 않으면 article_seq를 null로 두라. 한 기사에 사진 여러 장 가능. "
        '반드시 JSON만: {"assignments":[{"image_id":정수,"article_seq":정수 또는 null}]}'
    )
    user = f"## 기사\n{art_lines}\n\n## 사진\n{img_lines}"
    res = get_llm().complete_json(system, user, temperature=0.0)
    out: dict[int, int | None] = {}
    for a in res.get("assignments", []) or []:
        try:
            seq = a.get("article_seq")
            out[int(a["image_id"])] = int(seq) if seq is not None else None
        except (KeyError, ValueError, TypeError):
            continue
    return out


def match_message_images(conn, message_pk: int, primary_attachment_id: int,
                         tenant_id: int = db.DEFAULT_TENANT, use_llm: bool = True) -> dict:
    """메시지 전체 이미지(zip+임베드) → 기사 매칭. 독립 단계('퍼즐 맞추기').

    단건이면 전부 그 기사로. 다건이면 LLM이 제목·파일명으로 배정, 빈 곳은 번호 정규식 폴백.
    """
    articles = db.list_articles(conn, primary_attachment_id, tenant_id=tenant_id)
    imgs = [im for im in db.list_message_images(conn, message_pk, tenant_id=tenant_id)
            if im["selected"]]
    stats = {"articles": len(articles), "images": len(imgs), "matched": 0, "unmatched": 0,
             "llm": False}
    if not articles or not imgs:
        return stats

    by_seq = {a["sequence_number"]: a["id"] for a in articles}
    if len(articles) == 1:
        for im in imgs:
            db.assign_image_article(conn, im["id"], articles[0]["id"], tenant_id=tenant_id)
        stats["matched"] = len(imgs)
        conn.commit()
        return stats

    assign: dict[int, int | None] = {}
    if use_llm:
        try:
            assign = _llm_assign(articles, imgs)
            stats["llm"] = True
        except Exception:  # noqa: BLE001 (LLM 실패 시 정규식 폴백)
            assign = {}

    for im in imgs:
        seq = assign.get(im["id"])
        if seq is None:                       # LLM 미배정 → 파일명 번호 폴백
            seq = _seq_from_name(im["orig_name"] or im["path"])
        target = by_seq.get(seq)
        db.assign_image_article(conn, im["id"], target, tenant_id=tenant_id)
        stats["matched" if target else "unmatched"] += 1
    conn.commit()
    return stats


def match_images_to_articles(conn, attachment_id: int, tenant_id: int = db.DEFAULT_TENANT) -> dict:
    """(레거시·단일첨부) 한 첨부의 이미지를 그 첨부의 기사들에 매칭."""
    articles = db.list_articles(conn, attachment_id, tenant_id=tenant_id)
    imgs = [im for im in db.list_images(conn, attachment_id, tenant_id=tenant_id) if im["selected"]]
    stats = {"matched": 0, "unmatched": 0}
    if not articles or not imgs:
        return stats
    by_seq = {a["sequence_number"]: a["id"] for a in articles}
    for im in imgs:
        if len(articles) == 1:
            db.assign_image_article(conn, im["id"], articles[0]["id"], tenant_id=tenant_id)
            stats["matched"] += 1
            continue
        target = by_seq.get(_seq_from_name(im["orig_name"] or im["path"]))
        db.assign_image_article(conn, im["id"], target, tenant_id=tenant_id)
        stats["matched" if target else "unmatched"] += 1
    return stats


def _measure(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.width, im.height
    except Exception:  # noqa: BLE001
        return 0, 0


def _load_classifier(use_gemini: bool):
    if not use_gemini:
        return None
    try:
        from .generator import classify_image  # 지연 임포트(키 없으면 사용 안 함)
        return classify_image
    except Exception:  # noqa: BLE001
        return None


def _save_one(conn, att_id: int, tenant_id: int, idx: int, data: bytes, ext: str,
              orig_name: str | None, classify, stats: dict, source: str | None = None):
    """이미지 1장: 크기 필터 → (가능하면)Gemini 분류 → 저장. classify를 반환(실패시 None)."""
    w, h = _measure(data)
    if w and h and max(w, h) < _MIN_SIDE:
        stats["skipped_small"] += 1
        return classify
    kind, selected, caption = "unknown", False, ""
    if classify:
        try:
            res = classify(data, ext)
            kind, selected, caption = res.kind, res.is_article_photo, res.caption
        except Exception:  # noqa: BLE001
            classify = None
    if not classify:
        selected = bool(w and h and max(w, h) >= _PHOTO_SIDE)
        kind = "photo" if selected else "unknown"
    key = f"images/{tenant_id}/{att_id}/{idx}.{ext}"
    get_storage().put(key, data, mime_for(ext))
    db.insert_image(
        conn, tenant_id=tenant_id, attachment_id=att_id, path=key, orig_name=orig_name,
        source=source, ext=ext, width=w, height=h, bytes=len(data), kind=kind,
        selected=1 if selected else 0, caption=caption, ord=idx)
    stats["saved"] += 1
    if selected:
        stats["selected"] += 1
    return classify


def process_zip_images(conn, att_id: int, files, use_gemini: bool = True,
                       tenant_id: int = db.DEFAULT_TENANT) -> dict:
    """ZIP에서 푼 이미지(ExpandedFile 목록)를 저장. 원본 파일명(orig_name) 보존."""
    db.clear_images(conn, att_id, tenant_id=tenant_id)
    stats = {"total": 0, "saved": 0, "selected": 0, "skipped_small": 0}
    classify = _load_classifier(use_gemini)
    for idx, f in enumerate(files):
        if not getattr(f, "is_image", False):
            continue
        stats["total"] += 1
        classify = _save_one(conn, att_id, tenant_id, idx, f.data, f.ext, f.name, classify, stats)
    conn.commit()
    return stats


def process_images(conn, att_id: int, draft: ArticleDraft, use_gemini: bool = True,
                   tenant_id: int = db.DEFAULT_TENANT, source: str | None = None) -> dict:
    """draft.images(문서 임베드/웹 이미지)를 저장·선별. source 주면 출처로 기록."""
    db.clear_images(conn, att_id, tenant_id=tenant_id)
    stats = {"total": 0, "saved": 0, "selected": 0, "skipped_small": 0}
    classify = _load_classifier(use_gemini)
    for idx, img in enumerate(draft.images):
        stats["total"] += 1
        classify = _save_one(conn, att_id, tenant_id, idx, img.data, img.ext,
                             None, classify, stats, source=source)
    conn.commit()
    return stats
