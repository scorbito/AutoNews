"""발행 어댑터. 활성 어댑터를 get_publisher() 로 가져온다.

발행 대상이 정해지면 여기서 활성 구현만 바꾸면 호출부는 그대로다.
"""
from __future__ import annotations

import os

from .base import Publisher, PublishResult
from .export_html import HtmlExportPublisher


def is_production() -> bool:
    """실제 운영 서버인지. APP_ENV=production 또는 Railway 환경이면 운영."""
    if os.getenv("APP_ENV", "").lower() in ("production", "prod"):
        return True
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")
                or os.getenv("RAILWAY_SERVICE_ID"))


def cms_configured(config: dict | None = None) -> bool:
    """이 테넌트가 실제 CMS 발행에 필요한 설정(사이트·아이디·비번)을 갖췄는지.

    atpaju(ND소프트)·wordpress 모두 같은 3개 필드(ndsoft_base_url·cms_user·cms_password)를 쓴다.
    """
    c = config or {}
    if str(c.get("publisher") or "").lower() not in ("atpaju", "wordpress"):
        return False
    return (bool(c.get("cms_user")) and bool(c.get("cms_password"))
            and bool(c.get("ndsoft_base_url")))


def get_publisher(config: dict | None = None) -> Publisher:
    """발행기 선택.

    - 실제 운영서버 + CMS 설정 완료 → 발행기(atpaju / wordpress) 실발행
    - 로컬이거나 CMS 미설정 → HTML(발행 게시판 미리보기). 실수로도 실제 발행 안 됨.
    """
    if is_production() and cms_configured(config):
        pub = str((config or {}).get("publisher") or "").lower()
        if pub == "wordpress":
            from .wordpress import WordPressPublisher
            return WordPressPublisher(config)
        from .atpaju import AtpajuPublisher
        return AtpajuPublisher(config, live=True)
    return HtmlExportPublisher()


__all__ = ["Publisher", "PublishResult", "get_publisher", "is_production", "cms_configured"]
