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


def match_images_to_articles(conn, attachment_id: int, tenant_id: int = db.DEFAULT_TENANT) -> dict:
    """추출된 이미지를 기사에 매칭 (이식명세 §6).
    단건이면 전부 그 기사로. 다건이면 파일명 번호(사진N-M / N_) 로 매칭, 실패 시 미매칭(검토)."""
    articles = db.list_articles(conn, attachment_id, tenant_id=tenant_id)
    imgs = db.list_images(conn, attachment_id, tenant_id=tenant_id)
    stats = {"matched": 0, "unmatched": 0}
    if not articles or not imgs:
        return stats

    by_seq = {a["sequence_number"]: a["id"] for a in articles}
    for im in imgs:
        if not im["selected"]:
            continue
        if len(articles) == 1:
            db.assign_image_article(conn, im["id"], articles[0]["id"], tenant_id=tenant_id)
            stats["matched"] += 1
            continue
        # 다건: 파일명에서 선두 번호 추출 (basename 기준, 폴더 prefix 무시)
        import os
        base = os.path.basename(im["path"] or "")
        m = re.search(r"(?:사진|photo)?\s*(\d+)[-_]", base) or re.match(r"\s*(\d+)", base)
        seq = int(m.group(1)) if m else None
        target = by_seq.get(seq)
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
              orig_name: str | None, classify, stats: dict):
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
        ext=ext, width=w, height=h, bytes=len(data), kind=kind,
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
                   tenant_id: int = db.DEFAULT_TENANT) -> dict:
    """draft.images(문서 임베드 이미지)를 저장·선별. 파일명이 없어 orig_name=None."""
    db.clear_images(conn, att_id, tenant_id=tenant_id)
    stats = {"total": 0, "saved": 0, "selected": 0, "skipped_small": 0}
    classify = _load_classifier(use_gemini)
    for idx, img in enumerate(draft.images):
        stats["total"] += 1
        classify = _save_one(conn, att_id, tenant_id, idx, img.data, img.ext, None, classify, stats)
    conn.commit()
    return stats
