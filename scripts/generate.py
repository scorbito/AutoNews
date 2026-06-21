"""기사 초안 생성 실행.

  python scripts/generate.py            # 추출완료(done)된 첨부 전부 기사화
  python scripts/generate.py <att_id>   # 특정 첨부 1건만

DB의 추출 텍스트(extract_status='done')를 Gemini에 넘겨 초안을 만들고,
결과를 data/drafts/ 에 저장한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db                       # noqa: E402
from aute_news.generator import generate_article  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "drafts"


def render(a) -> str:
    s = [f"# {a.headline}", f"## {a.subheadline}", "", a.lead, "", a.body, "",
         "■ 3줄 요약", *[f"- {x}" for x in a.summary], "",
         f"■ 태그: {', '.join(a.tags)}"]
    if a.confidence_notes.strip():
        s += ["", f"■ [기자 확인 필요] {a.confidence_notes}"]
    return "\n".join(s)


def main() -> None:
    conn = db.connect()
    q = "SELECT id, filename, extracted_text FROM attachments WHERE extract_status='done' AND extracted_text IS NOT NULL"
    if len(sys.argv) > 1:
        q += f" AND id={int(sys.argv[1])}"
    rows = conn.execute(q).fetchall()
    conn.close()
    if not rows:
        print("기사화할 추출 텍스트가 없습니다.")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for r in rows:
        print(f"\n=== [att {r['id']}] {r['filename']} → 기사 생성 중 ...")
        try:
            article = generate_article(r["extracted_text"], source_title=r["filename"])
        except Exception as e:  # noqa: BLE001
            print(f"  실패: {type(e).__name__}: {e}")
            continue
        text = render(article)
        path = OUT / f"draft_{r['id']}.md"
        path.write_text(text, encoding="utf-8")
        print(f"  제목: {article.headline}")
        print(f"  저장: {path}")


if __name__ == "__main__":
    main()
