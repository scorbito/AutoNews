"""추출완료 첨부의 본문을 기사 N개로 분할해 articles 테이블에 저장.

  python scripts/split.py <att_id>     # 특정 첨부
  python scripts/split.py              # 추출완료(done)인데 아직 분할 안 한 것 전부
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db                # noqa: E402
from aute_news.split import run_split   # noqa: E402


def split_one(conn, att) -> None:
    msg = conn.execute(
        "SELECT subject, sender, body_text FROM messages WHERE id=?",
        (att["message_pk"],)).fetchone()
    res = run_split(
        att["extracted_text"],
        subject=msg["subject"] if msg else "",
        from_name=(msg["sender"] or "").split("<")[0].strip() if msg else "",
        body_text_preview=(msg["body_text"] or "")[:1500] if msg else "",
    )
    arts = res.get("articles", [])
    db.clear_articles(conn, att["id"])
    for a in arts:
        db.insert_article(
            conn, attachment_id=att["id"],
            sequence_number=a.get("sequence_number", 1),
            title=a.get("title", ""), body=a.get("body", ""),
            contact_info=json.dumps(a.get("contact_info"), ensure_ascii=False),
            category_hint=a.get("category_hint"), status="split")
    conn.commit()
    print(f"  [att {att['id']}] {len(arts)}개 기사 분할 "
          f"(conf={res.get('split_confidence')}) {res.get('warnings') or ''}")
    for a in arts:
        print(f"     #{a.get('sequence_number')}: {a.get('title','')[:40]}")


def main() -> None:
    conn = db.connect()
    if len(sys.argv) > 1:
        rows = conn.execute("SELECT * FROM attachments WHERE id=?", (int(sys.argv[1]),)).fetchall()
    else:
        rows = conn.execute(
            """SELECT a.* FROM attachments a
               WHERE a.extract_status='done' AND a.extracted_text IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM articles ar WHERE ar.attachment_id=a.id)""").fetchall()
    if not rows:
        print("분할할 첨부가 없습니다.")
        return
    for att in rows:
        try:
            split_one(conn, att)
        except Exception as e:  # noqa: BLE001
            print(f"  [att {att['id']}] 분할 실패: {type(e).__name__}: {e}")
    conn.close()


if __name__ == "__main__":
    main()
