"""발행 어댑터 인터페이스.

발행 대상(워드프레스/자체CMS/네이버 등)이 정해지면 이 인터페이스를 구현해
교체한다. 상위 코드(웹/스크립트)는 Publisher.publish() 만 호출하므로
대상이 바뀌어도 발행 호출부는 그대로다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PublishResult:
    ok: bool
    url: str = ""        # 발행된 글의 URL 또는 파일 경로
    message: str = ""    # 실패 사유 등


class Publisher:
    name = "base"

    def publish(self, ref_id: int, headline: str, content: str,
                images: list[dict] | None = None, *, category: str | None = None,
                subtitle: str = "", body_is_html: bool = False) -> PublishResult:
        """기사 발행.
        content: body_is_html=True 면 HTML 그대로, 아니면 마크다운(변환).
        images: [{'path':..., 'caption':...}], category: 섹션코드, subtitle: 부제."""
        raise NotImplementedError
