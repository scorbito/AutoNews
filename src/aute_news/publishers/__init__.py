"""발행 어댑터. 활성 어댑터를 get_publisher() 로 가져온다.

발행 대상이 정해지면 여기서 활성 구현만 바꾸면 호출부는 그대로다.
"""
from __future__ import annotations

import os

from .base import Publisher, PublishResult
from .export_html import HtmlExportPublisher


def get_publisher() -> Publisher:
    """활성 발행기 선택. 기본은 안전한 HTML 내보내기.
    PUBLISHER=atpaju 로 설정해야 실제 사이트 발행기를 사용한다."""
    if os.getenv("PUBLISHER", "html").lower() == "atpaju":
        from .atpaju import AtpajuPublisher
        return AtpajuPublisher()
    return HtmlExportPublisher()


__all__ = ["Publisher", "PublishResult", "get_publisher"]
