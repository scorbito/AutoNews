"""예약 수집 실행 — 외부 스케줄러(작업 스케줄러/cron)가 주기 호출.

  python scripts/run_scheduled.py [--window N]

지금(KST) 수집예약(collect_times)이 걸린 테넌트만 수집+처리한다.
window(기본 5분)는 호출 주기와 맞춰라(예: 5분마다 호출 → --window 5).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news.scheduler import run_due  # noqa: E402


def main() -> None:
    window = 5
    if "--window" in sys.argv:
        try:
            window = int(sys.argv[sys.argv.index("--window") + 1])
        except (ValueError, IndexError):
            pass
    results = run_due(window)
    if not results:
        print("이번 시각에 예약된 테넌트 없음.")
        return
    for r in results:
        print(f"[tenant {r['tenant_id']}] 수집={r['collect']} | 기사 {r['articles_made']}건")


if __name__ == "__main__":
    main()
