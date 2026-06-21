"""수집 실행 진입점.

  python scripts/collect.py            # 네이버+다음 모두
  python scripts/collect.py naver      # 네이버만

새 메일만 가져와(UID 추적) DB(data/aute_news.db)에 저장하고,
첨부는 data/attachments/ 에 저장 + 지원 형식은 즉시 추출한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news.collector import collect_all  # noqa: E402


def main() -> None:
    targets = sys.argv[1:] or None
    for s in collect_all(targets):
        if s.get("skipped"):
            print(f"[{s['account']}] 건너뜀: {s['skipped']}")
            continue
        print(f"[{s['account']}] 새 메일 {s['new_messages']}건 | "
              f"첨부 {s['attachments']}개 (추출 {s['extracted']} / 수동 {s['manual']})")


if __name__ == "__main__":
    main()
