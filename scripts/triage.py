"""수집된 메일을 Triage 분류.

  python scripts/triage.py          # 미분류 메일만
  python scripts/triage.py --all    # 전체 재분류
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db                       # noqa: E402
from aute_news.triage import build_meta, run_triage  # noqa: E402


def main() -> None:
    only_new = "--all" not in sys.argv
    conn = db.connect()
    rows = db.messages_to_triage(conn, only_new=only_new)
    if not rows:
        print("분류할 메일이 없습니다.")
        return
    for r in rows:
        atts = db.message_attachments(conn, r["id"])
        meta = build_meta(r["subject"], r["sender"], r["body_text"], atts)
        try:
            res = run_triage(meta)
        except Exception as e:  # noqa: BLE001
            print(f"  [msg {r['id']}] 분류 실패: {type(e).__name__}: {e}")
            continue
        db.set_triage(conn, r["id"], res["pipeline"], res.get("triage_confidence"),
                      res.get("reasoning", ""))
        print(f"  [msg {r['id']}] {res['pipeline']:14} "
              f"conf={res.get('triage_confidence')} | 첨부 {len(atts)} | {r['subject'][:30]}")
    conn.close()


if __name__ == "__main__":
    main()
