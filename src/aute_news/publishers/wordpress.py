"""워드프레스 발행 어댑터 — REST API(wp/v2) + Application Password.

워드프레스 5.6+ 내장 '응용 프로그램 비밀번호'로 Basic 인증해 글/미디어를 올린다.
워드프레스 기반 신문사는 사이트 주소만 다르고 엔드포인트는 동일하므로 그대로 재사용된다.

설정(tenant_config) 재사용:
  ndsoft_base_url → 워드프레스 사이트 주소 (https://mypaper.com)
  cms_user        → WP 사용자명(로그인 ID)
  cms_password    → WP 응용 프로그램 비밀번호 (사용자 → 프로필 → 응용 프로그램 비밀번호)
  cms_section     → (선택) 기본 카테고리 ID(숫자). 비우면 기본 카테고리
  cms_auto_submit → 체크 시 즉시 게시(publish), 해제 시 초안(draft)으로 저장(편집자 검토)

엔드포인트:
  POST {base}/wp-json/wp/v2/media   이미지 업로드 → media id, source_url
  POST {base}/wp-json/wp/v2/posts   글 등록(title, content, status, categories, featured_media)
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from .base import Publisher, PublishResult
from ..storage import get_storage, mime_for

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")


def _kill_switch() -> bool:
    return os.getenv("PUBLISH_DISABLED", "") in ("1", "true", "True")


class WordPressPublisher(Publisher):
    name = "wordpress"

    def __init__(self, config: dict | None = None) -> None:
        c = config or {}
        self.base = (c.get("ndsoft_base_url") or os.getenv("WP_BASE_URL", "")).rstrip("/")
        self.user = c.get("cms_user") or os.getenv("WP_USER", "")
        self.app_pw = c.get("cms_password") or os.getenv("WP_APP_PASSWORD", "")
        sec = str(c.get("cms_section") or "").strip()
        self.default_cat = int(sec) if sec.isdigit() else None
        # 체크 시 즉시 게시, 해제 시 초안(편집자가 WP에서 검토 후 발행)
        self.status = "publish" if c.get("cms_auto_submit") else "draft"

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.auth = (self.user, self.app_pw)          # Basic 인증(앱 비밀번호; 공백은 WP가 서버에서 제거)
        s.headers.update({"User-Agent": UA})
        return s

    def _upload_image(self, s: requests.Session, im: dict) -> dict | None:
        """이미지 1장 업로드 → {'id':.., 'url':.., 'caption':..} 또는 None."""
        raw = get_storage().get(im["path"])
        if not raw:
            return None
        ext = Path(im["path"]).suffix.lstrip(".").lower() or "jpg"
        fname = Path(im["path"]).name or f"image.{ext}"
        r = s.post(f"{self.base}/wp-json/wp/v2/media", data=raw, timeout=60,
                   headers={"Content-Disposition": f'attachment; filename="{fname}"',
                            "Content-Type": mime_for(ext)})
        r.raise_for_status()
        j = r.json()
        cap = im.get("caption") or ""
        if im.get("source"):
            cap = (cap + " " if cap else "") + f"사진 제공: {im['source']}"
        return {"id": j.get("id"), "url": j.get("source_url", ""), "caption": cap}

    def publish(self, ref_id: int, headline: str, content: str,
                images: list[dict] | None = None, *, category: str | None = None,
                subtitle: str = "", body_is_html: bool = False) -> PublishResult:
        if _kill_switch():
            return PublishResult(False, message="PUBLISH_DISABLED — 발행 보류(dry-run)")
        if not (self.base and self.user and self.app_pw):
            return PublishResult(False, message="워드프레스 설정 부족(사이트·아이디·앱비번)")

        s = self._session()
        try:
            # 1) 이미지 업로드 → 본문 끝에 figure 로 첨부, 첫 장은 대표이미지
            uploaded, featured = [], None
            for im in (images or []):
                try:
                    u = self._upload_image(s, im)
                except Exception:  # noqa: BLE001 (이미지 1장 실패는 본문 발행을 막지 않음)
                    u = None
                if u and u.get("url"):
                    uploaded.append(u)
                    if featured is None and u.get("id"):
                        featured = u["id"]

            import html as _html
            parts = []
            if subtitle:
                parts.append(f"<p><strong>{_html.escape(subtitle)}</strong></p>")
            parts.append(content if body_is_html else _html.escape(content))
            for u in uploaded:
                cap = f"<figcaption>{_html.escape(u['caption'])}</figcaption>" if u["caption"] else ""
                parts.append(f'<figure><img src="{_html.escape(u["url"])}"/>{cap}</figure>')
            body_html = "\n".join(parts)

            # 2) 글 등록
            payload = {"title": headline or "(제목 없음)", "content": body_html,
                       "status": self.status}
            if subtitle:
                payload["excerpt"] = subtitle
            if self.default_cat:
                payload["categories"] = [self.default_cat]
            if featured:
                payload["featured_media"] = featured
            r = s.post(f"{self.base}/wp-json/wp/v2/posts", json=payload, timeout=60)
            r.raise_for_status()
            j = r.json()
            url = j.get("link") or f"{self.base}/?p={j.get('id', '')}"
            note = "게시" if self.status == "publish" else "초안 저장"
            return PublishResult(True, url=url, message=f"워드프레스 {note}")
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.text[:200]
            except Exception:  # noqa: BLE001
                pass
            code = getattr(e.response, "status_code", "?")
            return PublishResult(False, message=f"워드프레스 발행 실패(HTTP {code}): {body}")
        except Exception as e:  # noqa: BLE001
            return PublishResult(False, message=f"워드프레스 발행 실패: {type(e).__name__}: {e}")
        finally:
            s.close()
