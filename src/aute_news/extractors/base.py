"""추출 결과의 정규화 포맷과 추출기 인터페이스.

모든 입력 형식(HWP/HWPX/PDF/웹링크)은 추출 후 ArticleDraft 한 가지로 통일된다.
이후 단계(LLM 기사화·UI·발행)는 원본이 무엇이었는지 몰라도 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ImageAsset:
    """원문에서 추출한 이미지(본문 텍스트와 별개)."""
    data: bytes
    ext: str = "png"          # png/jpg/gif ...
    source_ref: str = ""      # 원본 내 식별자(스트림명/xref/URL)
    width: int = 0
    height: int = 0
    caption: str = ""


@dataclass
class ArticleDraft:
    """추출된 기사 원문의 정규화 포맷."""
    source_format: str            # "hwp" | "hwpx" | "pdf" | "weblink"
    title: str = ""
    body_text: str = ""
    images: list[ImageAsset] = field(default_factory=list)
    source_url: str | None = None
    source_name: str = ""         # 매체명(웹 출처 표기용, 예: "뉴스와이어")
    extracted_at: datetime | None = None


class ExtractError(Exception):
    """추출 실패. 호출부는 이를 잡아 '수동 처리 큐'로 보낸다."""


class Extractor:
    """형식별 추출기 공통 인터페이스."""
    source_format: str = ""

    def can_handle(self, filename: str) -> bool:
        raise NotImplementedError

    def extract(self, path: str) -> ArticleDraft:
        raise NotImplementedError
