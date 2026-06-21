"""atpaju(ND소프트 템플릿) 로그인 + 기사작성 폼 정찰.

목적: 로그인한 세션으로 기사작성 폼을 열어 '실제 등록 엔드포인트(action)'와
      'input/select/textarea 필드명'을 알아낸다. (기사 등록은 하지 않음 — 읽기만)

사용:
  .env 에 ATPAJU_ID / ATPAJU_PW 를 넣고
  python scripts/atpaju_recon.py
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

BASE = "https://www.atpaju.com"
LOGIN = f"{BASE}/member/login.php"
WRITE_FORM = f"{BASE}/news/userArticleWriteForm.html"   # 새 기사 ID 부여 + 폼
WRITE_POST = f"{BASE}/news/userArticleWrite.php"         # 실제 등록 엔드포인트
UA = {"User-Agent": "Mozilla/5.0"}

FIELD_RE = re.compile(
    r"<(input|select|textarea)\b[^>]*?\bname=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
TYPE_RE = re.compile(r'\btype=["\']([^"\']+)["\']', re.IGNORECASE)
VALUE_RE = re.compile(r'\bvalue=["\']([^"\']*)["\']', re.IGNORECASE)
ACTION_RE = re.compile(r'<form\b[^>]*\baction=["\']([^"\']+)["\']', re.IGNORECASE)


def main() -> None:
    uid, pw = os.getenv("ATPAJU_ID"), os.getenv("ATPAJU_PW")
    if not uid or not pw:
        print(".env 에 ATPAJU_ID / ATPAJU_PW 를 넣어주세요.")
        return

    s = requests.Session()
    s.headers.update(UA)
    s.get(f"{BASE}/member/login.html", timeout=20)  # 초기 쿠키
    r = s.post(LOGIN, data={"user_id": uid, "user_pw": pw, "backUrl": "", "id_save": ""},
               timeout=20, allow_redirects=True)

    body = r.text
    if "정보를 바르게" in body or "비밀번호" in body and "alert" in body:
        print("로그인 실패로 보입니다. 응답:", body[:200])
        return
    print("로그인 응답 OK. 쿠키:", list(s.cookies.keys()))

    w = s.get(WRITE_FORM, timeout=20)
    html = w.text
    if "로그인을 해주세요" in html:
        print("기사폼 접근 실패(세션 미인정). 로그인 방식 재점검 필요.")
        print("write 응답 일부:", html[:200])
        return

    Path("data").mkdir(exist_ok=True)
    Path("data/atpaju_writeform.html").write_text(html, encoding="utf-8")

    print(f"\n기사작성 폼 접근 성공 ({len(html)} bytes)")
    actions = ACTION_RE.findall(html)
    print("폼 action(들):", actions, " (예상 등록 주소:", WRITE_POST, ")")
    print("\n필드 목록 (userArticleWrite.php 로 보낼 후보):")
    id_like = []
    for tag, name in FIELD_RE.findall(html):
        seg = html[max(0, html.find(name) - 80): html.find(name) + 120]
        typ = TYPE_RE.search(seg)
        val = VALUE_RE.search(seg)
        tval = typ.group(1) if typ else ""
        vval = val.group(1) if val else ""
        mark = ""
        if tval == "hidden" and (vval.strip().isdigit() or re.search(r"id|idx|no|seq", name, re.I)):
            mark = "   ← 기사 ID 후보"
            id_like.append((name, vval))
        print(f"  - {name:26} <{tag}{(' type='+tval) if tval else ''}>"
              f"{(' value='+vval[:40]) if vval else ''}{mark}")
    if id_like:
        print("\n부여된 기사 ID 후보:", id_like)
    print("\n전체 폼 HTML 저장: data/atpaju_writeform.html")


if __name__ == "__main__":
    main()
