"""이미지 처리 — 추출된 이미지를 저장하고 '기사용 사진'만 선별.

선별 2단계:
  1) 크기 필터 — 한 변이 너무 작으면(아이콘/직인 추정) 비채택
  2) (API 키 있으면) Gemini 멀티모달 분류 — 사진 vs 직인/로고/서식
저장: data/images/{att_id}/{n}.{ext}  +  images 테이블
"""
from __future__ import annotations

import hashlib
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


def _llm_assign_articles(articles: list, imgs: list) -> dict:
    """여러 문서의 기사가 섞인 경우 — 사진을 기사 id에 배정. {image_id: article_id|None}."""
    from .llm import get_llm
    art_lines = "\n".join(
        f"  [{a['id']}] {a['headline'] or a['title'] or ''}" for a in articles)
    img_lines = "\n".join(f"  [{im['id']}] {_img_name(im)}" for im in imgs)
    system = (
        "너는 보도자료 사진을 기사에 배정하는 편집 보조다. "
        "기사 목록([기사id] 제목)과 사진 목록([사진id] 파일명)이 주어진다. "
        "각 사진을 가장 알맞은 기사 id에 배정하라(파일명에 담긴 인물·제목 단서 사용). "
        "어느 기사에도 안 맞으면 article_id를 null로. "
        '반드시 JSON만: {"assignments":[{"image_id":정수,"article_id":정수 또는 null}]}'
    )
    user = f"## 기사\n{art_lines}\n\n## 사진\n{img_lines}"
    res = get_llm().complete_json(system, user, temperature=0.0)
    out: dict[int, int | None] = {}
    for a in res.get("assignments", []) or []:
        try:
            aid = a.get("article_id")
            out[int(a["image_id"])] = int(aid) if aid is not None else None
        except (KeyError, ValueError, TypeError):
            continue
    return out


def match_message_images_all(conn, message_pk: int, tenant_id: int = db.DEFAULT_TENANT,
                             use_llm: bool = True) -> dict:
    """메일의 모든 문서 기사 ↔ 모든 이미지 매칭(문서 여러 개인 메일용). LLM이 제목·파일명으로 배정."""
    articles = db.list_message_articles(conn, message_pk, tenant_id=tenant_id)
    imgs = [im for im in db.list_message_images(conn, message_pk, tenant_id=tenant_id)
            if im["selected"]]
    stats = {"articles": len(articles), "images": len(imgs), "matched": 0, "unmatched": 0}
    if not articles or not imgs:
        return stats
    if len(articles) == 1:
        for im in imgs:
            db.assign_image_article(conn, im["id"], articles[0]["id"], tenant_id=tenant_id)
        stats["matched"] = len(imgs)
        conn.commit()
        return stats
    assign = {}
    if use_llm:
        try:
            assign = _llm_assign_articles(articles, imgs)
        except Exception:  # noqa: BLE001
            assign = {}
    valid = {a["id"] for a in articles}
    for im in imgs:
        aid = assign.get(im["id"])
        if aid not in valid:
            aid = None
        db.assign_image_article(conn, im["id"], aid, tenant_id=tenant_id)
        stats["matched" if aid else "unmatched"] += 1
    conn.commit()
    return stats


def dedup_article_images(conn, message_pk: int, tenant_id: int = db.DEFAULT_TENANT) -> int:
    """기사에 배정된 이미지 중 시각적으로 같은 사진은 1장만 남기고 나머지 연결 해제.

    수집/저장은 중복 허용 — '기사에 넣을 때'만 중복 제거. 해제된 장수 반환.
    """
    arts = conn.execute(
        """SELECT ar.id FROM articles ar JOIN attachments a ON a.id=ar.attachment_id
           WHERE a.message_pk=? AND ar.tenant_id=?""", (message_pk, tenant_id)).fetchall()
    store = get_storage()
    removed = 0
    for ar in arts:
        seen: list = []
        for im in db.list_article_images(conn, ar["id"], tenant_id=tenant_id):
            data = store.get(im["path"])
            if not data:
                continue
            ah = _ahash(data)
            if ah is None:
                ah = int(hashlib.md5(data).hexdigest()[:16], 16)  # noqa: S324
            if _is_visual_dup(ah, seen):
                db.assign_image_article(conn, im["id"], None, tenant_id=tenant_id)
                removed += 1
            else:
                seen.append(ah)
    conn.commit()
    return removed


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


def _ahash(data: bytes) -> int | None:
    """average hash(8x8 그레이스케일) — 해상도·압축이 달라도 같은 사진이면 거의 동일."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            px = list(im.convert("L").resize((8, 8)).getdata())
    except Exception:  # noqa: BLE001
        return None
    avg = sum(px) / len(px)
    bits = 0
    for i, p in enumerate(px):
        if p > avg:
            bits |= 1 << i
    return bits


def _is_visual_dup(ahash: int, seen: list) -> bool:
    """seen(aHash 목록) 중 해밍거리 5 이하면 같은 사진으로 본다."""
    return any(bin(ahash ^ s).count("1") <= 5 for s in seen)


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
    """이미지 1장: 크기 필터 → (가능하면)Gemini 분류 → 저장. (중복 허용; 기사 배정 때 제거)"""
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
    # 결정적 백스톱: 해상도 대비 용량이 극히 작으면(단색 로고/CI/슬로건 그래픽)
    # Gemini가 photo라 해도 기사 사진에서 제외(실사진은 픽셀당 바이트가 훨씬 큼).
    if selected and w and h and len(data) / (w * h) < 0.08:
        selected, kind = False, "graphic"
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
        classify = _save_one(conn, att_id, tenant_id, idx, f.data, f.ext, f.name,
                             classify, stats)
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
