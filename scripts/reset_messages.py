"""(개발용) 한 테넌트의 수집 데이터 초기화 → 처음부터 재수집 가능하게.

  python scripts/reset_messages.py --tenant 8           # 미리보기(삭제 안 함)
  python scripts/reset_messages.py --tenant 8 --yes     # 실제 삭제

삭제 대상(해당 tenant): images → articles → attachments → messages → folder_state(UID 기준선).
이후 '내 설정 ②'에서 '전체 수집(collect_all)'을 켜고 📥 메일 수집하면
첨부를 다시 받아 이미지가 (현재 STORAGE_BACKEND) 저장소에 재저장됩니다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db  # noqa: E402


def main() -> None:
    if "--tenant" not in sys.argv:
        print("사용법: python scripts/reset_messages.py --tenant <id> [--yes]")
        return
    tid = int(sys.argv[sys.argv.index("--tenant") + 1])
    do_it = "--yes" in sys.argv

    conn = db.connect()
    counts = {
        "images": conn.execute("SELECT COUNT(*) c FROM images WHERE tenant_id=?", (tid,)).fetchone()["c"],
        "articles": conn.execute("SELECT COUNT(*) c FROM articles WHERE tenant_id=?", (tid,)).fetchone()["c"],
        "attachments": conn.execute("SELECT COUNT(*) c FROM attachments WHERE tenant_id=?", (tid,)).fetchone()["c"],
        "messages": conn.execute("SELECT COUNT(*) c FROM messages WHERE tenant_id=?", (tid,)).fetchone()["c"],
    }
    print(f"[tenant {tid}] 삭제 예정:", counts)

    if not do_it:
        print("미리보기입니다. 실제 삭제하려면 --yes 를 붙이세요.")
        conn.close()
        return

    for tbl in ("images", "articles", "attachments", "messages", "folder_state"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE tenant_id=?", (tid,))
        except Exception as e:  # noqa: BLE001 (folder_state 등 없을 수 있음)
            print(f"  - {tbl} 건너뜀: {type(e).__name__}")
    conn.commit()
    conn.close()
    print(f"[tenant {tid}] 초기화 완료. '내 설정 ②'에서 전체 수집(collect_all) 켜고 메일 수집하세요.")


if __name__ == "__main__":
    main()
