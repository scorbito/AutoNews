"""
메일 IMAP 접속 검증 스크립트.

사용법:
  1) .env.example 을 복사해 .env 를 만들고 계정/앱비밀번호 입력
  2) python scripts/imap_test.py            # 네이버+다음 둘 다 시도
     python scripts/imap_test.py naver       # 네이버만
     python scripts/imap_test.py daum        # 다음만

결과: 최근 메일 10건의 제목/발신자/날짜/첨부 목록을 출력하고,
      data/imap_test_result.txt 에도 UTF-8 로 저장(콘솔 한글 깨짐 대비).
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from imap_tools import MailBox, AND

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "imap_test_result.txt"

ACCOUNTS = {
    "naver": {
        "host": "imap.naver.com",
        "email": os.getenv("NAVER_EMAIL"),
        "password": os.getenv("NAVER_PASSWORD"),
    },
    "daum": {
        "host": "imap.daum.net",
        "email": os.getenv("DAUM_EMAIL"),
        "password": os.getenv("DAUM_PASSWORD"),
    },
}


def check(name: str, cfg: dict, lines: list[str]) -> None:
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    emit(f"\n{'='*60}\n[{name}] {cfg['host']}\n{'='*60}")
    if not cfg["email"] or not cfg["password"]:
        emit(f"  건너뜀: .env 에 {name.upper()}_EMAIL / {name.upper()}_PASSWORD 가 없음")
        return
    try:
        with MailBox(cfg["host"]).login(cfg["email"], cfg["password"]) as mb:
            emit(f"  로그인 성공: {cfg['email']}")
            emit(f"  폴더 목록: {[f.name for f in mb.folder.list()]}")
            emit("  --- 최근 메일 10건 ---")
            count = 0
            # reverse=True: 최신순, bulk=False: 한 건씩(안정적)
            for msg in mb.fetch(reverse=True, limit=10, mark_seen=False, bulk=False):
                count += 1
                atts = [f"{a.filename} ({len(a.payload)}B)" for a in msg.attachments]
                emit(f"  [{count}] {msg.date_str} | {msg.from_}")
                emit(f"      제목: {msg.subject}")
                if atts:
                    emit(f"      첨부: {', '.join(atts)}")
            if count == 0:
                emit("  (받은편지함이 비어있음)")
            emit(f"  => 총 {count}건 조회 성공")
    except Exception as e:  # noqa: BLE001
        emit(f"  로그인/조회 실패: {type(e).__name__}: {e}")
        emit("  ↳ 점검: IMAP 사용 설정 ON? 앱 비밀번호 사용? 이메일 주소 형식?")


def main() -> None:
    targets = sys.argv[1:] or ["naver", "daum"]
    lines: list[str] = []
    for name in targets:
        if name not in ACCOUNTS:
            print(f"알 수 없는 계정: {name} (naver/daum 중 선택)")
            continue
        check(name, ACCOUNTS[name], lines)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n결과 저장: {OUT}")


if __name__ == "__main__":
    main()
