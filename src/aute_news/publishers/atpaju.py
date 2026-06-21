"""ND소프트 신문 템플릿용 발행 어댑터 (atpaju.com 에서 검증).

ND소프트 CMS 는 사이트마다 도메인만 다르고 엔드포인트·필드 구조는 동일하므로,
base URL(NDSOFT_BASE_URL)만 바꾸면 ND소프트를 쓰는 다른 언론사에도 재사용된다.
(워드프레스 등 다른 CMS 는 별도 Publisher 를 구현하면 됨 — 프레임워크가 받쳐줌.)

n8n 워크플로에서 확인된 실제 요청을 그대로 재현한다:
  1) POST /member/login.php           로그인 → 세션 쿠키
  2) GET  /news/userArticleWriteForm.html  → HTML 에서 idxno 추출
  3) POST /news/userArticleWrite.php   기사 등록(mode=modify, idxno, 필드 일체)
  4) POST /news/quickUpload.ajax.php   (선택) 이미지 업로드

설정은 .env 로:
  NDSOFT_BASE_URL(기본 https://www.atpaju.com)  발행 대상 사이트 도메인
  ATPAJU_ID, ATPAJU_PW           로그인 계정
  ATPAJU_USER_NAME(기본 '작업자'), ATPAJU_USER_EMAIL
  ATPAJU_SECTION(기본 'S2N2')    섹션(카테고리) 코드
  ATPAJU_LIVE=1                  실제 게시(onoff=O). 미설정/0 이면 dry-run(등록 안 함)
"""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

import requests

from .base import Publisher, PublishResult
from .export_html import _content_to_html

DEFAULT_BASE = "https://www.atpaju.com"   # NDSOFT_BASE_URL 로 다른 사이트 지정 가능
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")

# ============================================================
# 하드 안전잠금: 실제 사이트 게시를 코드 레벨에서 전면 차단.
# True 인 동안에는 .env 의 ATPAJU_LIVE=1 이어도 절대 게시되지 않고
# dry-run(로그인·기사ID 확인까지만)으로만 동작한다.
# 정말로 실제 발행을 허용할 때만, 직접 이 값을 False 로 바꿀 것.
# ============================================================
HARD_DISABLE_LIVE = True


def _split_subtitle(content: str) -> tuple[str, str]:
    """저장된 마크다운에서 부제(첫 '## ')를 분리하고, 제목/부제 줄을 본문에서 제거."""
    subtitle, body = "", []
    for ln in (content or "").splitlines():
        s = ln.rstrip()
        if s.startswith("# "):
            continue                      # 제목은 headline 으로 따로 전송
        if s.startswith("## ") and not subtitle:
            subtitle = s[3:]
            continue
        body.append(ln)
    return subtitle, "\n".join(body)


class AtpajuPublisher(Publisher):
    name = "atpaju"

    def __init__(self) -> None:
        self.base = os.getenv("NDSOFT_BASE_URL", DEFAULT_BASE).rstrip("/")
        self.uid = os.getenv("ATPAJU_ID", "")
        self.pw = os.getenv("ATPAJU_PW", "")
        self.user_name = os.getenv("ATPAJU_USER_NAME", "작업자")
        self.user_email = os.getenv("ATPAJU_USER_EMAIL", "")
        self.section = os.getenv("ATPAJU_SECTION", "S1N10")  # 기본 미분류(명세 §7)
        self.live = os.getenv("ATPAJU_LIVE", "") in ("1", "true", "True")

    def _login(self, s: requests.Session) -> None:
        s.headers.update({"User-Agent": UA})
        s.get(f"{self.base}/member/login.html", timeout=20)
        s.post(f"{self.base}/member/login.php",
               data={"backUrl": "", "user_id": self.uid, "user_pw": self.pw},
               headers={"Referer": f"{self.base}/member/login.html", "Origin": self.base},
               timeout=20, allow_redirects=False)

    def _get_idxno(self, s: requests.Session) -> str | None:
        r = s.get(f"{self.base}/news/userArticleWriteForm.html", timeout=20)
        if "로그인을 해주세요" in r.text:
            return None                   # 세션 미인정 = 로그인 실패
        m = re.search(r"idxno=(\d+)", r.text)
        return m.group(1) if m else None

    def _write(self, s: requests.Session, idxno: str, headline: str,
               subtitle: str, body_html: str, pub_date: str,
               section: str | None = None) -> requests.Response:
        data = {
            "mode": "modify", "idxno": idxno,
            "sectionCode": section or self.section, "subSectionCode": "",
            "title": headline, "subTitle": subtitle, "FCKeditor1": body_html,
            "pub_date": pub_date,
            "user_id": self.uid, "user_name": self.user_name, "user_email": self.user_email,
            "article_source": "self", "onoff": "O", "view_level": "A",
            "view_recognition": "Y", "area": "D", "autoSave": "1", "uora": "U",
            "level": "B", "article_type": "B", "serial_number": "0", "page": "0",
            "pdf": "N", "embargo": "N", "recognition": "I",
            "keyword": "", "shoulder_title": "", "portal_title": "",
        }
        return s.post(f"{self.base}/news/userArticleWrite.php", data=data,
                      headers={"Referer": f"{self.base}/news/userArticleWriteForm.html"},
                      timeout=30)

    def _upload_image(self, s: requests.Session, idxno: str, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        s.post(f"{self.base}/news/quickUpload.ajax.php",
               data={"mode": "input", "article_idxno": idxno, "reverse": "Y", "search": "Y"},
               files={"uploadFile1[0]": (p.name, p.read_bytes(), mime)},
               headers={"X-Requested-With": "XMLHttpRequest", "Origin": self.base,
                        "Referer": f"{self.base}/news/userArticleWriteForm.html?mode=modify&idxno={idxno}"},
               timeout=60)

    def publish(self, ref_id: int, headline: str, content: str,
                images: list[dict] | None = None, *, category: str | None = None,
                subtitle: str = "", body_is_html: bool = False) -> PublishResult:
        if not self.uid or not self.pw:
            return PublishResult(False, message="ATPAJU_ID/ATPAJU_PW 가 .env 에 없습니다.")

        import datetime
        pub_date = datetime.date.today().isoformat()
        if body_is_html:
            body_html = content                       # 기사(article_body_html) 그대로
        else:
            sub2, body_md = _split_subtitle(content)  # 레거시 마크다운 초안
            subtitle = subtitle or sub2
            body_html = _content_to_html(body_md)
        section = category or self.section

        s = requests.Session()
        try:
            self._login(s)
            idxno = self._get_idxno(s)
            if not idxno:
                return PublishResult(False, message="로그인 실패 또는 idxno 추출 실패")

            if HARD_DISABLE_LIVE or not self.live:
                reason = "하드잠금(HARD_DISABLE_LIVE)" if HARD_DISABLE_LIVE else "ATPAJU_LIVE 미설정"
                return PublishResult(
                    True, url=f"(dry-run) idxno={idxno} 발급 성공 — 실제 게시 차단됨",
                    message=f"dry-run: 로그인·기사ID까지만 검증. 등록 안 함 ({reason})")

            self._write(s, idxno, headline, subtitle, body_html, pub_date, section)
            for im in images or []:
                self._upload_image(s, idxno, im["path"])
            url = f"{self.base}/news/articleView.html?idxno={idxno}"
            return PublishResult(True, url=url)
        except requests.RequestException as e:
            return PublishResult(False, message=f"발행 요청 실패: {e}")
        finally:
            s.close()
