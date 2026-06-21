"""atpaju 발행 어댑터 검증.

기본은 dry-run: 로그인 → 기사ID(idxno) 발급까지만 확인하고 실제 등록은 안 한다.
실제 게시까지 하려면 .env 에 ATPAJU_LIVE=1 을 설정하고 실행.

  python scripts/atpaju_publish_test.py          # dry-run (안전)
  python scripts/atpaju_publish_test.py <att_id>  # 특정 초안으로 시도
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from aute_news import db                              # noqa: E402
from aute_news.publishers.atpaju import AtpajuPublisher  # noqa: E402


def main() -> None:
    conn = db.connect()
    q = "SELECT attachment_id, headline, content FROM drafts WHERE content IS NOT NULL"
    if len(sys.argv) > 1:
        q += f" AND attachment_id={int(sys.argv[1])}"
    q += " LIMIT 1"
    row = conn.execute(q).fetchone()
    imgs = []
    if row:
        imgs = [{"path": im["path"], "caption": im["caption"]}
                for im in db.list_images(conn, row["attachment_id"]) if im["selected"]]
    conn.close()
    if not row:
        print("초안(content)이 있는 항목이 없습니다. 먼저 UI에서 초안을 생성하세요.")
        return

    pub = AtpajuPublisher()
    print(f"대상: att {row['attachment_id']} | {row['headline']}")
    print(f"모드: {'LIVE(실제게시)' if pub.live else 'dry-run(검증만)'} | 섹션: {pub.section} | 이미지 {len(imgs)}장")
    res = pub.publish(row["attachment_id"], row["headline"], row["content"], imgs)
    print("결과 ok:", res.ok)
    print("url   :", res.url)
    print("메시지:", res.message)


if __name__ == "__main__":
    main()
