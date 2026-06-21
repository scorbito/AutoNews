"""TXT 추출기 — UTF-8 → CP949(EUC-KR) 폴백 (이식명세 §3, 함정 #2).

한국 공공기관 txt 는 대부분 CP949. UTF-8 강제 디코딩하면 전부 깨져
AI 환각·빈기사를 유발하므로 반드시 폴백한다.
"""
from __future__ import annotations

from datetime import datetime

from .base import ArticleDraft, ExtractError, Extractor


def decode_best(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")  # 최후: 손실 허용


class TxtExtractor(Extractor):
    source_format = "txt"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".txt")

    def extract(self, path: str) -> ArticleDraft:
        with open(path, "rb") as f:
            raw = f.read()
        body = decode_best(raw).replace("\r\n", "\n").strip()
        if not body:
            raise ExtractError("본문 텍스트가 비어있음")
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        return ArticleDraft(
            source_format=self.source_format,
            title=first, body_text=body, extracted_at=datetime.now(),
        )
