"""Triage (이식명세 §5) — 메일 메타 → 7 파이프라인 분류.

추출 전에, 메일의 발신자·제목·첨부목록·링크만 보고 어떤 변주인지 분류한다.
  PUBLIC_MULTI / PUBLIC_SINGLE / LINK_BASED / NON_STANDARD /
  BODY_AS_ARTICLE / NEEDS_REVIEW / SKIP
결과(pipeline)는 messages 에 저장되어 후속 라우팅에 쓰인다.
"""
from __future__ import annotations

import json
import re

from .extractors import detect_format
from .llm import get_llm, load_prompt

PIPELINES = {"PUBLIC_MULTI", "PUBLIC_SINGLE", "LINK_BASED", "NON_STANDARD",
             "BODY_AS_ARTICLE", "NEEDS_REVIEW", "SKIP"}

_PRESS_EXTS = {"hwp", "hwpx", "pdf", "doc", "docx", "txt"}
_IMG_EXTS = {"jpg", "jpeg", "png", "gif", "bmp"}
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# 공공기관 도메인 신호
_PUBLIC_HINTS = (".go.kr", ".korea.kr", ".gov", ".or.kr", ".ac.kr",
                 "city.", "council", "시청", "도청", "교육청")
# 다운로드 링크로 보이는 신호
_DL_HINTS = ("kmmailer.korea.kr", "drive.google", "/download", "/file",
             ".hwp", ".hwpx", ".pdf", ".docx", ".zip")
# 추적/구독취소 등 무시할 링크
_SKIP_LINK_HINTS = ("unsubscribe", "수신거부", "/track", "pixel", "googleusercontent")


def _domain(addr: str) -> str:
    return addr.split("@")[-1].lower() if "@" in (addr or "") else ""


def _looks_download(url: str) -> bool:
    u = url.lower()
    if any(h in u for h in _SKIP_LINK_HINTS):
        return False
    return any(h in u for h in _DL_HINTS)


def build_meta(subject: str, sender: str, body_text: str,
               attachments: list[dict]) -> dict:
    """Triage 입력용 메일 메타 구성."""
    addr = ""
    m = re.search(r"[\w.+-]+@[\w.-]+", sender or "")
    if m:
        addr = m.group(0)
    domain = _domain(addr)

    atts = []
    for i, a in enumerate(attachments):
        ext = detect_format(a["filename"])
        if ext == "other":
            ext = a["filename"].rsplit(".", 1)[-1].lower() if "." in a["filename"] else ""
        atts.append({
            "key": f"attachment_{i}",
            "filename": a["filename"],
            "ext": ext,
            "size": a.get("size", 0),
            "is_press_format": ext in _PRESS_EXTS,
            "is_image": ext in _IMG_EXTS,
            "is_archive": ext == "zip",
        })

    links = []
    for u in dict.fromkeys(_URL_RE.findall(body_text or "")):  # 중복 제거
        if any(h in u.lower() for h in _SKIP_LINK_HINTS):
            continue
        links.append({"url": u.rstrip(".,)"), "looks_like_download": _looks_download(u)})

    # 본문 번호목록(1. 2. 3.) 개수 = 다건 힌트
    numbered = len(re.findall(r"(?m)^\s*\d+\.\s+\S", body_text or ""))

    return {
        "subject": subject or "",
        "from_name": (sender or "").split("<")[0].strip(),
        "from_address": addr,
        "is_known_public_domain": any(h in domain for h in _PUBLIC_HINTS),
        "attachment_count": len(atts),
        "attachments": atts,
        "links": links[:20],
        "subject_count_hint": numbered,
        "body_text_preview": (body_text or "")[:1500],
    }


def run_triage(meta: dict) -> dict:
    """메타 → LLM Triage → 분류 결과 dict."""
    system, _ = load_prompt("Triage")
    user = ("## 입력 메일 메타\n\n아래 JSON 메일을 분류해 스키마대로 JSON 으로 응답하세요.\n\n"
            "```json\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n```")
    result = get_llm().complete_json(system, user, temperature=0.2)
    if result.get("pipeline") not in PIPELINES:
        result["pipeline"] = "NEEDS_REVIEW"
    return result
