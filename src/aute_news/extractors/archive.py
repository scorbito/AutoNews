"""압축(ZIP) 해제 도구 — '묶인 걸 푼다' (코드 영역, 판단 없음).

ZIP 첨부를 풀어 내부 파일(주로 사진)을 원본 파일명과 함께 돌려준다.
한글 파일명(보통 CP437로 저장된 CP949/EUC-KR)을 복원한다.
어느 사진이 어느 기사인지 같은 '맞추기'는 하지 않는다 — 그건 LLM의 몫.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff"}


@dataclass
class ExpandedFile:
    """ZIP 안에서 꺼낸 파일 1개."""
    name: str          # 복원된 원본 파일명(디렉터리 제외)
    data: bytes
    ext: str           # 소문자 확장자(점 없음)

    @property
    def is_image(self) -> bool:
        return self.ext in IMAGE_EXTS


# zip 기반이지만 '문서'인 형식 — 사진 번들 zip 으로 오인하면 안 된다.
_ZIP_DOC_EXT = (".hwpx", ".docx", ".xlsx", ".pptx", ".hwp", ".pdf")


def is_zip(filename: str = "", data: bytes | None = None) -> bool:
    low = (filename or "").lower()
    if low.endswith(".zip"):
        return True
    if low.endswith(_ZIP_DOC_EXT):     # hwpx/docx 등은 내부가 zip(PK)이지만 문서임
        return False
    return bool(data) and len(data) >= 2 and data[:2] == b"PK"


def _decode_name(info: zipfile.ZipInfo) -> str:
    """ZIP 엔트리 파일명 복원.

    UTF-8 플래그가 있으면 zipfile이 이미 정상 디코드. 없으면 zipfile은 CP437로
    디코드해두는데, 그게 (a) 이미 정상 한글이면 그대로 쓰고, (b) CP437 모지바케면
    CP437→CP949로 되돌린다.
    """
    name = info.filename
    if info.flag_bits & 0x800:          # 언어 인코딩 플래그(UTF-8)
        return name
    try:
        rebytes = name.encode("cp437")
    except UnicodeEncodeError:
        return name                     # CP437로 못 넣는 문자 = 이미 정상 디코드된 한글 등
    for enc in ("cp949", "euc-kr"):
        try:
            return rebytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def expand_zip(data: bytes, images_only: bool = True) -> list[ExpandedFile]:
    """ZIP 바이트를 풀어 파일 목록 반환. images_only면 이미지 확장자만."""
    out: list[ExpandedFile] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = _decode_name(info)
            base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if not base or base.startswith(".") or "__MACOSX" in name:
                continue
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            if images_only and ext not in IMAGE_EXTS:
                continue
            out.append(ExpandedFile(name=base, data=z.read(info), ext=ext))
    return out
