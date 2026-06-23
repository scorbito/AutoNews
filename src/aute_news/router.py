"""LLM 라우터 — 메일을 통째로 보고 '어떻게 처리할지' 계획을 세운다(트리아지 승격판).

코드 휴리스틱(다운로드 URL 패턴, 본문이 기사냐 다이제스트냐) 대신 LLM이 판단한다.
자료의 실제 '추출'은 여전히 코드가 한다 — 라우터는 '무엇을 어떻게'만 정한다.

반환: {skip, reason, body_is_article, article_links[], download_links[]}  (실패 시 None)
"""
from __future__ import annotations

import re

from .llm import get_llm

_SYSTEM = (
    "너는 언론사 보도자료 메일을 받아 '어떻게 처리할지' 계획을 세우는 라우터다. "
    "제목·발신·첨부 파일명·본문·본문 내 링크를 보고 판단하라.\n"
    "- skip: 기사거리가 전혀 아니면 true(광고/스팸/단순 안내). 단, 보도자료를 "
    "링크나 첨부로 전달하는 메일은 skip 아님(그 자료를 처리해야 함).\n"
    "- body_is_article: 본문 자체가 기사로 쓸 보도자료 '본문'이면 true. "
    "인사말·목차·파일안내·링크목록뿐이면 false.\n"
    "- article_links: 본문 링크 중 '웹 기사 페이지'(열면 기사 본문이 보이는 URL)만.\n"
    "- download_links: 본문 링크 중 '파일 다운로드'(hwp/pdf/이미지 등 자료를 내려받는 URL)만.\n"
    "로고·홈·구독관리·수신거부·추적 링크는 article/download 둘 다에서 제외. "
    "첨부파일 자체(hwp/이미지 등)는 코드가 따로 처리하니 링크 목록에 넣지 마라. "
    "링크는 반드시 입력에 주어진 URL 중에서만 고른다.\n"
    '반드시 JSON만: {"skip":bool,"reason":"...","body_is_article":bool,'
    '"article_links":["..."],"download_links":["..."]}'
)


def plan_email(subject: str, sender: str, body: str,
               attachment_names: list[str], link_candidates: list[dict]) -> dict | None:
    cand_urls = {c["url"] for c in link_candidates}
    link_lines = "\n".join(f"  - {c['url']} | {c['context'][:80]}"
                           for c in link_candidates) or "  (없음)"
    att_lines = "\n".join(f"  - {n}" for n in attachment_names) or "  (없음)"
    text = re.sub(r"<[^>]+>", " ", body or "")          # 태그 제거 본문(판단용)
    text = re.sub(r"\s+", " ", text).strip()[:4000]
    user = (f"## 제목\n{subject}\n\n## 발신\n{sender}\n\n## 첨부 파일명\n{att_lines}\n\n"
            f"## 본문(일부)\n{text}\n\n## 본문 내 링크 후보\n{link_lines}")
    try:
        r = get_llm().complete_json(_SYSTEM, user, temperature=0.0)
    except Exception:  # noqa: BLE001 (실패 → 호출부가 휴리스틱 폴백)
        return None

    def _pick(key: str) -> list[str]:
        out = []
        for u in (r.get(key) or []):
            if isinstance(u, str):
                u = u.replace("&amp;", "&")
                if u in cand_urls:               # 환각 방지: 실제 후보만
                    out.append(u)
        return list(dict.fromkeys(out))

    return {
        "skip": bool(r.get("skip")),
        "reason": str(r.get("reason") or "")[:300],
        "body_is_article": bool(r.get("body_is_article")),
        "article_links": _pick("article_links"),
        "download_links": _pick("download_links"),
    }
