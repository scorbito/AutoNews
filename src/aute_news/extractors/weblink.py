"""웹링크 추출기 — trafilatura 기반.

메일 본문에 기사 URL 이 들어온 경우, 해당 페이지의 본문을 추출한다.
첨부파일과 달리 입력이 '파일 경로'가 아니라 'URL' 이므로 extract_url() 을 사용한다.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import trafilatura

from .base import ArticleDraft, ExtractError, ImageAsset

_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp)(\?|$)", re.IGNORECASE)


def _download_images(downloaded: str, base_url: str, meta) -> list[ImageAsset]:
    """og:image 와 본문 <img> 중 기사 이미지를 받아온다(최대 5장)."""
    urls: list[str] = []
    if meta and getattr(meta, "image", None):
        urls.append(meta.image)
    for m in _IMG_SRC_RE.finditer(downloaded or ""):
        src = urljoin(base_url, m.group(1))
        if _IMG_EXT_RE.search(src) and src not in urls:
            urls.append(src)

    images: list[ImageAsset] = []
    for u in urls[:8]:
        try:
            req = Request(u, headers={"User-Agent": "Mozilla/5.0 aute_news"})
            with urlopen(req, timeout=10) as resp:  # noqa: S310
                data = resp.read()
        except Exception:  # noqa: BLE001
            data = None
        if isinstance(data, bytes) and len(data) > 1024:  # 1KB↑만(아이콘 제외)
            m = _IMG_EXT_RE.search(u)
            ext = (m.group(1).lower() if m else "jpg").replace("jpeg", "jpg")
            images.append(ImageAsset(data=data, ext=ext, source_ref=u))
        if len(images) >= 5:
            break
    return images

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")

# 본문 기사 URL 이 아닐 가능성이 큰 도메인/패턴(수신거부·추적·이미지 등)
_SKIP = ("unsubscribe", "/track", "mailto:", ".jpg", ".png", ".gif",
         "list-manage", "doubleclick", "utm_", "/pixel")


def find_article_url(text: str) -> str | None:
    """본문 텍스트에서 기사로 보이는 첫 URL 추출(노이즈 URL 제외)."""
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,)")
        if not any(s in url.lower() for s in _SKIP):
            return url
    return None


def extract_url(url: str) -> ArticleDraft:
    """URL 페이지 본문 추출."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise ExtractError(f"페이지를 가져오지 못함: {url}")
    body = trafilatura.extract(
        downloaded, include_comments=False, include_tables=True, favor_recall=True
    )
    if not body or not body.strip():
        raise ExtractError(f"본문 추출 실패(동적 페이지/차단 가능): {url}")

    meta = trafilatura.extract_metadata(downloaded)
    title = (meta.title if meta and meta.title else body.strip().splitlines()[0])[:200]
    return ArticleDraft(
        source_format="weblink",
        title=title,
        body_text=body.strip(),
        images=_download_images(downloaded, url, meta),
        source_url=url,
        extracted_at=datetime.now(),
    )
