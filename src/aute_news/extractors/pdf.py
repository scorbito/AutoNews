"""PDF 추출기 — PyMuPDF(fitz) 기반.

텍스트 레이어가 있는 PDF는 직접 추출한다.
텍스트가 거의 없으면 스캔본(이미지)으로 판단해 수동 큐로 보낸다
(향후 OCR 또는 Gemini 멀티모달로 처리).
"""
from __future__ import annotations

import re
from datetime import datetime

import fitz  # PyMuPDF

from .base import ArticleDraft, ExtractError, Extractor, ImageAsset

# 이 글자 수 미만이면 스캔본으로 간주
_MIN_TEXT_CHARS = 30


def _clean(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)      # 줄끝 공백 제거
    text = re.sub(r"\n{3,}", "\n\n", text)      # 빈 줄 과다 정리
    return text.strip()


class PdfExtractor(Extractor):
    source_format = "pdf"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".pdf")

    def extract(self, path: str) -> ArticleDraft:
        try:
            doc = fitz.open(path)
        except Exception as e:  # noqa: BLE001
            raise ExtractError(f"PDF 열기 실패: {e}") from e

        try:
            if doc.needs_pass:
                raise ExtractError("암호 설정 PDF — 자동 추출 불가")
            pages = [p.get_text("text") for p in doc]
            images = []
            seen = set()
            for page in doc:
                for info in page.get_images(full=True):
                    xref = info[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    img = doc.extract_image(xref)  # {'image': bytes, 'ext': 'png', ...}
                    images.append(ImageAsset(
                        data=img["image"], ext=img.get("ext", "png"),
                        source_ref=f"xref{xref}",
                        width=img.get("width", 0), height=img.get("height", 0)))
        finally:
            doc.close()

        body = _clean("\n".join(pages))
        if len(body) < _MIN_TEXT_CHARS:
            raise ExtractError("텍스트 레이어 없음(스캔본 추정) — OCR/멀티모달 필요")

        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        return ArticleDraft(
            source_format=self.source_format,
            title=first,
            body_text=body,
            images=images,
            extracted_at=datetime.now(),
        )
