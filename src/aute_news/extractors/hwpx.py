"""HWPX 추출기 — zip + XML(OWPML).

HWPX 는 zip 컨테이너이며 본문은 Contents/section0.xml, section1.xml ... 에 들어있다.
문단은 <hp:p>, 텍스트 런은 <hp:t> 로 표현된다(네임스페이스는 버전마다 다를 수 있어
local-name 으로 매칭한다).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

from .base import ArticleDraft, ExtractError, Extractor, ImageAsset

_IMG_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # '{ns}t' -> 't'


def _paragraph_text(p_elem: ET.Element) -> str:
    """문단 요소 하위의 모든 <t> 텍스트를 이어붙임."""
    parts: list[str] = []
    for el in p_elem.iter():
        if _local(el.tag) == "t":
            parts.append("".join(el.itertext()))
    return "".join(parts)


def _extract_section(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    paras: list[str] = []
    for el in root.iter():
        if _local(el.tag) == "p":
            text = _paragraph_text(el).strip()
            if text:
                paras.append(text)
    return paras


class HwpxExtractor(Extractor):
    source_format = "hwpx"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".hwpx")

    def extract(self, path: str) -> ArticleDraft:
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise ExtractError(f"HWPX(zip) 열기 실패: {e}") from e

        try:
            sections = sorted(
                n for n in zf.namelist()
                if re.match(r"Contents/section\d+\.xml$", n)
            )
            if not sections:
                raise ExtractError("Contents/section*.xml 없음 — HWPX 구조 아님")
            paras: list[str] = []
            for name in sections:
                paras.extend(_extract_section(zf.read(name)))

            # 이미지: BinData/ 폴더의 그림 파일
            images = []
            for n in sorted(zf.namelist()):
                if not n.startswith("BinData/"):
                    continue
                ext = n.rsplit(".", 1)[-1].lower() if "." in n else "bin"
                if ext in _IMG_EXTS:
                    images.append(ImageAsset(data=zf.read(n), ext=ext, source_ref=n))
        finally:
            zf.close()

        body = "\n".join(paras)
        if not body.strip():
            raise ExtractError("본문 텍스트를 찾지 못함")
        return ArticleDraft(
            source_format=self.source_format,
            title=paras[0] if paras else "",
            body_text=body,
            images=images,
            extracted_at=datetime.now(),
        )
