"""기존 수집 첨부 재추출.

새 추출기를 추가했거나 추출 로직을 고쳤을 때, 저장된 첨부 파일을 다시 추출해
DB의 extracted_text / extract_status 를 갱신한다.

  python scripts/reextract.py          # 'done' 이 아닌 것만 재처리
  python scripts/reextract.py --all    # 전부 재처리(로직 변경 검증용)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db, images                           # noqa: E402
from aute_news.extractors import ExtractError, extract_bytes  # noqa: E402
from aute_news.storage import get_storage                   # noqa: E402


def main() -> None:
    all_mode = "--all" in sys.argv
    conn = db.connect()
    q = "SELECT id, filename, path, extract_status FROM attachments"
    if not all_mode:
        q += " WHERE extract_status != 'done'"
    rows = conn.execute(q).fetchall()

    changed = 0
    for r in rows:
        try:
            data = get_storage().get(r["path"])
            if data is None:
                raise ExtractError("저장소에서 파일을 찾을 수 없음")
            draft = extract_bytes(data, r["filename"])
            text, status = draft.body_text, "done"
        except ExtractError as e:
            text, status = None, "manual"
            note = str(e)
        else:
            img_stat = images.process_images(conn, r["id"], draft, use_gemini="--no-ai" not in sys.argv)
            note = f"{len(text)}자, 이미지 {img_stat['saved']}장(채택 {img_stat['selected']})"
        conn.execute(
            "UPDATE attachments SET extracted_text=?, extract_status=? WHERE id=?",
            (text, status, r["id"]),
        )
        if status != r["extract_status"] or all_mode:
            changed += 1
        print(f"  [att {r['id']}] {r['filename'][:34]:36} {r['extract_status']:7} → {status:7} ({note})")
    conn.commit()
    conn.close()
    print(f"\n완료: {len(rows)}건 처리, {changed}건 상태변경")


if __name__ == "__main__":
    main()
