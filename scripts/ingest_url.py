"""웹링크(기사 URL)를 수집 항목으로 추가.

  python scripts/ingest_url.py https://news.example.com/article/123

해당 URL 본문을 추출해 messages/attachments(format='weblink')로 저장한다.
이후 UI 목록에 나타나고, 다른 항목과 똑같이 초안 생성/발행할 수 있다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db                                   # noqa: E402
from aute_news.extractors import ExtractError, extract_url  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python scripts/ingest_url.py <URL>")
        return
    url = sys.argv[1]
    try:
        draft = extract_url(url)
    except ExtractError as e:
        print(f"추출 실패: {e}")
        return

    conn = db.connect()
    pk = db.insert_message(
        conn, account="manual", folder="weblink", uid=0,
        message_id=f"weblink:{url}", subject=draft.title,
        sender="(웹링크)", date="", body_text=draft.source_url or url,
    )
    if pk is None:
        print("이미 추가된 URL 입니다.")
        conn.close()
        return
    att_id = db.insert_attachment(
        conn, message_pk=pk, filename=draft.title or url, format="weblink",
        path=url, size=len(draft.body_text), extracted_text=draft.body_text,
        extract_status="done",
    )
    conn.commit()
    conn.close()
    print(f"추가됨: att {att_id} | {draft.title}")
    print(f"  {len(draft.body_text)}자 추출 → UI 목록에서 초안 생성 가능")


if __name__ == "__main__":
    main()
