"""이미지 처리 — 추출된 이미지를 저장하고 '기사용 사진'만 선별.

선별 2단계:
  1) 크기 필터 — 한 변이 너무 작으면(아이콘/직인 추정) 비채택
  2) (API 키 있으면) Gemini 멀티모달 분류 — 사진 vs 직인/로고/서식
저장: data/images/{att_id}/{n}.{ext}  +  images 테이블
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

from . import db
from .extractors.base import ArticleDraft

IMG_DIR = Path(__file__).resolve().parents[2] / "data" / "images"

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


def process_images(conn, att_id: int, draft: ArticleDraft, use_gemini: bool = True,
                   tenant_id: int = db.DEFAULT_TENANT) -> dict:
    """draft.images 를 저장·선별해 DB에 기록. 통계 반환."""
    db.clear_images(conn, att_id, tenant_id=tenant_id)
    dest_dir = IMG_DIR / str(tenant_id) / str(att_id)
    stats = {"total": 0, "saved": 0, "selected": 0, "skipped_small": 0}

    classify = None
    if use_gemini:
        try:
            from .generator import classify_image  # 지연 임포트(키 없으면 사용 안 함)
            classify = classify_image
        except Exception:  # noqa: BLE001
            classify = None

    for idx, img in enumerate(draft.images):
        stats["total"] += 1
        w, h = (img.width, img.height) if img.width and img.height else _measure(img.data)

        # 1단계: 크기 필터
        if w and h and max(w, h) < _MIN_SIDE:
            stats["skipped_small"] += 1
            continue

        kind, selected, caption = "unknown", False, ""
        # 2단계: Gemini 분류(가능하면)
        if classify:
            try:
                res = classify(img.data, img.ext)
                kind, selected, caption = res.kind, res.is_article_photo, res.caption
            except Exception:  # noqa: BLE001
                classify = None  # 실패 시 이후는 크기 기준으로
        if not classify:
            # 크기 휴리스틱: 충분히 크면 사진으로 자동 채택
            selected = bool(w and h and max(w, h) >= _PHOTO_SIDE)
            kind = "photo" if selected else "unknown"

        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{idx}.{img.ext}"
        path.write_bytes(img.data)
        db.insert_image(
            conn, tenant_id=tenant_id, attachment_id=att_id, path=str(path), ext=img.ext,
            width=w, height=h, bytes=len(img.data), kind=kind,
            selected=1 if selected else 0, caption=caption, ord=idx,
        )
        stats["saved"] += 1
        if selected:
            stats["selected"] += 1

    conn.commit()
    return stats
