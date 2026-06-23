"""본문 링크 → 기사 소스. '코드가 푼다(링크 추출)' + 'LLM이 고른다(기사 링크 판별)'.

메일 본문에 든 링크 중 '재가공할 뉴스/보도자료 기사' 링크만 골라낸다.
로고·썸네일·추적·수신거부 링크는 제외. 최종 fetch/재작성은 파이프라인이 한다.
"""
from __future__ import annotations

import re

from .llm import get_llm

_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')
_ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js")
_SKIP_HINT = ("unsubscribe", "수신거부", "/logout", "login", "mailto:", "googleusercontent",
              "/property/img", "/thumb/", "tracking", "pixel", "utm_", "facebook.com",
              "twitter.com", "instagram.com", "youtube.com")


def extract_link_candidates(body: str, limit: int = 40) -> list[dict]:
    """본문에서 링크 후보 추출. [{url, context}] (자산·추적 링크 제외, 중복 제거)."""
    body = body or ""
    out, seen = [], set()
    for mobj in _URL_RE.finditer(body):
        url = mobj.group(0).replace("&amp;", "&").rstrip(").,;'\"")
        low = url.lower()
        if low.endswith(_ASSET_EXT) or any(h in low for h in _SKIP_HINT):
            continue
        if url in seen:
            continue
        seen.add(url)
        s, e = mobj.start(), mobj.end()
        context = re.sub(r"\s+", " ", body[max(0, s - 60):e + 20]).strip()
        out.append({"url": url, "context": context})
        if len(out) >= limit:
            break
    return out


def pick_article_urls(candidates: list[dict]) -> list[str]:
    """LLM(flash-lite)이 후보 중 '재가공할 기사 링크'만 고른다. URL 목록 반환."""
    if not candidates:
        return []
    lines = "\n".join(f"  - {c['url']}  | 주변텍스트: {c['context'][:80]}" for c in candidates)
    system = (
        "너는 보도자료 다이제스트 메일에서 '재작성할 뉴스/보도자료 기사 링크'만 고르는 편집 보조다. "
        "각 링크가 개별 기사 본문 페이지인지, 아니면 로고·목록·구독관리·홈·광고 같은 비기사 링크인지 판단한다. "
        "기사 본문 페이지만 골라라. 확실치 않으면 제외. "
        '반드시 JSON만: {"article_urls":["...", ...]}'
    )
    user = f"## 링크 후보\n{lines}"
    try:
        res = get_llm().complete_json(system, user, temperature=0.0)
        urls = [u for u in (res.get("article_urls") or []) if isinstance(u, str)]
    except Exception:  # noqa: BLE001
        urls = []
    # 후보에 실제로 있던 url 만(환각 방지), 순서/중복 정리
    cand = {c["url"] for c in candidates}
    seen, picked = set(), []
    for u in urls:
        u = u.replace("&amp;", "&")
        if u in cand and u not in seen:
            seen.add(u)
            picked.append(u)
    return picked
