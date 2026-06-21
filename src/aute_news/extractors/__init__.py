"""형식 감지 → 적절한 추출기로 디스패치.

새 형식 추가 시 _EXTRACTORS 에 추출기 인스턴스만 등록하면 된다.
확장자가 실제 내용과 다를 수 있어(예: .hwp 인데 zip) 매직바이트로 선판별한다.
"""
from __future__ import annotations

from .base import ArticleDraft, ExtractError
from .docx import DocxExtractor
from .hwp import HwpExtractor
from .hwpx import HwpxExtractor
from .pdf import PdfExtractor
from .txt import TxtExtractor
from .weblink import extract_url, find_article_url

_EXTRACTORS = [
    HwpExtractor(),
    HwpxExtractor(),
    PdfExtractor(),
    DocxExtractor(),
    TxtExtractor(),
]

_IMG_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff"}


def detect_format(filename: str) -> str:
    name = filename.lower()
    for ext in ("hwpx", "hwp", "pdf", "docx", "doc", "txt"):
        if name.endswith("." + ext):
            return ext
    return "other"


def sniff_format(path: str, filename: str) -> str:
    """매직바이트로 실제 포맷 보정 (이식명세 함정 #3).
    OLE2(D0CF11E0)=hwp계열, PK(504B)=zip계열(hwpx/docx). 확장자와 충돌 시 내용 우선."""
    declared = detect_format(filename)
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return declared
    is_ole = head[:4] == b"\xd0\xcf\x11\xe0"
    is_zip = head[:2] == b"PK"
    # .hwp 인데 실제로 zip 이면 → hwpx 로 (오저장 케이스)
    if declared == "hwp" and is_zip:
        return "hwpx"
    # .hwpx/.docx 인데 OLE2 이면 → hwp(구형)로
    if declared in ("hwpx", "docx") and is_ole:
        return "hwp"
    return declared


def extract_file(path: str, filename: str) -> ArticleDraft:
    """첨부파일 1건 추출. 매직바이트로 보정된 포맷에 맞는 추출기 사용."""
    fmt = sniff_format(path, filename)
    pseudo = filename if detect_format(filename) == fmt else f"x.{fmt}"
    for ex in _EXTRACTORS:
        if ex.can_handle(pseudo):
            return ex.extract(path)
    raise ExtractError(f"아직 지원하지 않는 형식: {filename} (감지: {fmt})")


def extract_bytes(data: bytes, filename: str) -> ArticleDraft:
    """바이트에서 추출(저장소가 디스크가 아닐 때). 임시파일 경유."""
    import os
    import tempfile
    suffix = os.path.splitext(filename)[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        return extract_file(tmp, filename)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def select_primary(attachments: list[dict]) -> dict | None:
    """같은 보도자료가 여러 포맷일 때 본문 추출용 1건 선택 (이식명세 §3 우선순위).
    이미지/zip 은 본문 후보에서 제외. attachments: [{'filename':..., ...}]"""
    from ..config import ATTACH_PRIORITY, NON_ARTICLE_EXTS
    best, best_score = None, -1
    for a in attachments:
        ext = detect_format(a["filename"])
        if ext == "other" or ext in NON_ARTICLE_EXTS:
            continue
        score = ATTACH_PRIORITY.get(ext, 0)
        if score > best_score:
            best, best_score = a, score
    return best


__all__ = ["ArticleDraft", "ExtractError", "detect_format", "sniff_format",
           "extract_file", "extract_bytes", "select_primary",
           "extract_url", "find_article_url"]
