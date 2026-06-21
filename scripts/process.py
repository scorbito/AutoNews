"""메일 처리 파이프라인 실행 (triage→split→generate→모드분기).

  python scripts/process.py <message_pk>     # 특정 메일
  python scripts/process.py                  # 미처리(기사 없는) 메일 전부
  PIPELINE_MODE=auto python scripts/process.py <pk>   # 자동 발행 모드
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db                       # noqa: E402
from aute_news.pipeline import process_message  # noqa: E402


def main() -> None:
    conn = db.connect()
    if len(sys.argv) > 1:
        pks = [int(sys.argv[1])]
    else:
        rows = conn.execute(
            """SELECT DISTINCT m.id FROM messages m
               JOIN attachments a ON a.message_pk=m.id
               WHERE a.extract_status='done'
                 AND NOT EXISTS (SELECT 1 FROM articles ar WHERE ar.attachment_id=a.id)
               ORDER BY m.id""").fetchall()
        pks = [r["id"] for r in rows]
    if not pks:
        print("처리할 메일이 없습니다.")
        return
    for pk in pks:
        r = process_message(conn, pk)
        tag = "건너뜀" if r.get("skipped") else f"기사 {len(r['articles'])}건"
        print(f"[msg {pk}] {r['pipeline']:14} mode={r['mode']} → {tag} {r.get('skipped') or ''}")
        for aid in r["articles"]:
            a = db.get_article(conn, aid)
            print(f"    art {aid} [{a['status']}] {a['category_code']} | {a['headline']}")
    conn.close()


if __name__ == "__main__":
    main()
