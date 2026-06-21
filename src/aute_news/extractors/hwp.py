"""HWP 5.0 (OLE 바이너리) 추출기 — 순수 Python (olefile + zlib).

검증 완료: data/test1.hwp, data/test2.hwp 본문 정상 추출.
PARA_TEXT 레코드의 인라인/확장 컨트롤 문자를 폭(1 또는 8 wchar)에 맞춰
건너뛰어 'secd'/'cold' 같은 제어 식별자 찌꺼기가 본문에 섞이지 않게 한다.
"""
from __future__ import annotations

import struct
import zlib
from datetime import datetime

import olefile

from .base import ArticleDraft, ExtractError, Extractor, ImageAsset

_IMG_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "wmf", "emf"}

HWPTAG_PARA_TEXT = 67

# PARA_TEXT 내 컨트롤 문자(0~31) 중 8 wchar(16바이트)를 차지하는 것들.
# 나머지(0,10,13,24~31)는 1 wchar. 10/13은 줄/문단 바꿈.
_WCHAR8 = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _decode_para_text(data: bytes) -> str:
    out: list[str] = []
    i, n = 0, len(data)
    while i + 2 <= n:
        code = data[i] | (data[i + 1] << 8)
        if code in _WCHAR8:        # 확장/인라인 컨트롤 → 8 wchar 통째로 건너뜀
            i += 16
            continue
        if code < 32:              # 문자 컨트롤 → 1 wchar
            if code in (10, 13):
                out.append("\n")
            i += 2
            continue
        out.append(chr(code))
        i += 2
    return "".join(out).strip()


def _iter_records(raw: bytes):
    """HWP 레코드 스트림 순회: (tag_id, level, data)."""
    i, n = 0, len(raw)
    while i + 4 <= n:
        header = struct.unpack("<I", raw[i:i + 4])[0]
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:          # 확장 크기
            size = struct.unpack("<I", raw[i:i + 4])[0]
            i += 4
        yield tag, raw[i:i + size]
        i += size


class HwpExtractor(Extractor):
    source_format = "hwp"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".hwp")

    def extract(self, path: str) -> ArticleDraft:
        if not olefile.isOleFile(path):
            raise ExtractError(f"OLE 형식이 아님(HWP 5.0 아님): {path}")
        try:
            ole = olefile.OleFileIO(path)
        except Exception as e:  # noqa: BLE001
            raise ExtractError(f"OLE 열기 실패: {e}") from e

        try:
            streams = {"/".join(s) for s in ole.listdir()}
            header = ole.openstream("FileHeader").read()
            # FileHeader 속성 플래그(offset 36, bit0=압축 bit1=암호 bit2=배포용)
            # 암호/배포용 문서는 BodyText 에 안내문구만 있고 본문은 암호화 → 자동 추출 불가
            flags = header[36] if len(header) > 36 else 0
            if flags & 0x02:
                raise ExtractError("암호 설정 문서 — 자동 추출 불가")
            if flags & 0x04:
                raise ExtractError("배포용 문서(DRM) — 자동 추출 불가")
            compressed = bool(flags & 0x01)

            paragraphs: list[str] = []
            # 본문은 BodyText/Section0, Section1 ... 로 나뉠 수 있음
            sections = sorted(s for s in streams if s.startswith("BodyText/Section"))
            for sec in sections:
                raw = ole.openstream(sec).read()
                if compressed:
                    raw = zlib.decompress(raw, -15)
                for tag, data in _iter_records(raw):
                    if tag == HWPTAG_PARA_TEXT:
                        text = _decode_para_text(data)
                        if text:
                            paragraphs.append(text)

            # 이미지: BinData 스토리지의 그림 스트림
            images = []
            for s in sorted(x for x in streams if x.startswith("BinData/")):
                ext = s.rsplit(".", 1)[-1].lower() if "." in s else "bin"
                if ext not in _IMG_EXTS:
                    continue
                blob = ole.openstream(s).read()
                if compressed:
                    try:
                        blob = zlib.decompress(blob, -15)
                    except zlib.error:
                        pass  # 비압축 항목일 수 있음
                images.append(ImageAsset(data=blob, ext=ext, source_ref=s))
        finally:
            ole.close()

        body = "\n".join(paragraphs)
        if not body.strip():
            raise ExtractError("본문 텍스트를 찾지 못함")
        # 첫 문단을 제목 후보로(뉴스 메일 첨부는 보통 첫 줄이 제목)
        title = paragraphs[0] if paragraphs else ""
        return ArticleDraft(
            source_format=self.source_format,
            title=title,
            body_text=body,
            images=images,
            extracted_at=datetime.now(),
        )
