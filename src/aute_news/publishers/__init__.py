"""발행 어댑터. 활성 어댑터를 get_publisher() 로 가져온다.

발행 대상이 정해지면 여기서 활성 구현만 바꾸면 호출부는 그대로다.
"""
from __future__ import annotations

import os

from .base import Publisher, PublishResult
from .export_html import HtmlExportPublisher


def get_publisher(config: dict | None = None) -> Publisher:
    """발행기 선택. config(테넌트 설정)의 publisher 우선, 없으면 .env PUBLISHER.
    기본은 안전한 HTML 내보내기. 'atpaju' 면 ND소프트 발행기(config 로 사이트별 설정)."""
    ptype = (config or {}).get("publisher") or os.getenv("PUBLISHER", "html")
    if (ptype or "html").lower() == "atpaju":
        from .atpaju import AtpajuPublisher
        return AtpajuPublisher(config)
    return HtmlExportPublisher()


__all__ = ["Publisher", "PublishResult", "get_publisher"]
