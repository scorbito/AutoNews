"""정기 자동결제 — 외부 스케줄러(cron)가 하루 1회 호출.

  python scripts/run_billing.py

만료(period_end)가 지난 active 구독을 빌링키로 청구하고, 성공 시 30일 연장한다.
실패하면 past_due 로 바꿔(게이트가 자동 정지). 다음 호출 때 재시도되지 않으니
필요하면 별도 재시도 정책을 둔다(지금은 1회).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db, notify, subscription  # noqa: E402


def main() -> None:
    conn = db.connect()
    ids = subscription.due_for_renewal(conn)
    conn.close()
    if not ids:
        print("청구 대상 없음.")
        return
    for tid in ids:
        conn = db.connect()
        try:
            ok = subscription.charge_renewal(conn, tid)
            print(f"[tenant {tid}] 정기결제 {'성공(30일 연장)' if ok else '실패(past_due)'}")
            if not ok:
                notify.report_failure("정기결제 실패", tid)
        except Exception as e:  # noqa: BLE001 (한 건 실패가 전체를 멈추지 않게)
            notify.report_failure("정기결제 처리 오류", tid, exc=e)
            print(f"[tenant {tid}] 오류: {type(e).__name__}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
