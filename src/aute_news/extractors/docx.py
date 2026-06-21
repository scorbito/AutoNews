"""DOCX 추출기 — python-docx (이식명세 §3)."""
from __future__ import annotations

import zipfile
from datetime import datetime

import docx

from .base import ArticleDraft, ExtractError, Extractor, ImageAsset

_IMG_EXTS = {"jpg", "jpeg", "png", "gif", "bmp"}


class DocxExtractor(Extractor):
    source_format = "docx"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".docx")

    def extract(self, path: str) -> ArticleDraft:
        try:
            doc = docx.Document(path)
        except Exception as e:  # noqa: BLE001
            raise ExtractError(f"DOCX 열기 실패: {e}") from e

        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        body = "\n".join(paras)
        if not body.strip():
            raise ExtractError("본문 텍스트를 찾지 못함")

        # 이미지: word/media/ 에서
        images = []
        try:
            with zipfile.ZipFile(path) as zf:
                for n in sorted(zf.namelist()):
                    if n.startswith("word/media/"):
                        ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
                        if ext in _IMG_EXTS:
                            images.append(ImageAsset(data=zf.read(n), ext=ext, source_ref=n))
        except zipfile.BadZipFile:
            pass

        return ArticleDraft(
            source_format=self.source_format,
            title=paras[0] if paras else "",
            body_text=body, images=images, extracted_at=datetime.now(),
        )
