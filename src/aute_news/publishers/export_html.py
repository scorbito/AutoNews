"""기본 발행 어댑터 — 완성 기사를 HTML 파일로 내보내기.

발행 대상이 정해지기 전의 기본 구현. data/published/{att_id}.html 로 저장하고
그 경로를 결과 URL 로 돌려준다. 추후 WordPressPublisher 등으로 교체.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

from .base import Publisher, PublishResult
from ..storage import get_storage, mime_for


def _content_to_html(content: str) -> str:
    out, in_list = [], False
    for line in (content or "").splitlines():
        s = line.rstrip()
        if s.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{html.escape(s[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if s.startswith("# "):
            out.append(f"<h1>{html.escape(s[2:])}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2>{html.escape(s[3:])}</h2>")
        elif s.startswith("■"):
            out.append(f"<p class='block'>{html.escape(s)}</p>")
        elif s:
            out.append(f"<p>{html.escape(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


class HtmlExportPublisher(Publisher):
    name = "html-export"

    def publish(self, ref_id: int, headline: str, content: str,
                images: list[dict] | None = None, *, category: str | None = None,
                subtitle: str = "", body_is_html: bool = False) -> PublishResult:
        att_id = ref_id
        body_block = content if body_is_html else _content_to_html(content)
        # 선택된 이미지를 data URI 로 임베드(파일 이동 없이 단일 HTML 완결)
        figs = []
        for im in images or []:
            raw = get_storage().get(im["path"])
            if not raw:
                continue
            ext = Path(im["path"]).suffix.lstrip(".").lower() or "png"
            mime = mime_for(ext)
            b64 = base64.b64encode(raw).decode()
            cap = html.escape(im.get("caption") or "")
            src = html.escape(im.get("source") or "")
            if src:
                cap = (cap + " " if cap else "") + f"사진 제공: {src}"
            figs.append(f"<figure><img src='data:{mime};base64,{b64}' style='max-width:100%'>"
                        f"<figcaption>{cap}</figcaption></figure>")
        img_html = "\n".join(figs)
        page = (
            "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            f"<title>{html.escape(headline)}</title>"
            "<style>body{font-family:'Malgun Gothic',sans-serif;max-width:720px;"
            "margin:40px auto;line-height:1.8;padding:0 16px;}"
            "h1{font-size:26px;}h2{color:#555;font-weight:500;}"
            "figure{margin:18px 0;}figcaption{color:#777;font-size:14px;text-align:center;}"
            ".block{background:#f5f6f8;padding:8px 12px;border-radius:6px;color:#444;}</style>"
            f"</head><body>\n<h1>{html.escape(headline)}</h1>"
            + (f"<h2>{html.escape(subtitle)}</h2>" if subtitle else "")
            + f"\n{body_block}\n{img_html}\n</body></html>"
        )
        key = f"published/{att_id}.html"
        get_storage().put(key, page.encode("utf-8"), "text/html")
        return PublishResult(ok=True, url=key)
