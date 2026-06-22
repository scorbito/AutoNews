"""한 메일 계정의 수집 데이터 초기화.

지정한 테넌트+계정(account=이메일)의 메일·첨부·기사·이미지·초안과
수집 기준선(folder_state)을 삭제한다. 다음 수집 때 '지금' 기준으로 재설정됨.

사용:
    python scripts/reset_account.py <tenant_id> <account_email>
    python scripts/reset_account.py 3 scorbit@naver.com
"""
import sys

sys.path.insert(0, "src")

from aute_news import db  # noqa: E402


def reset_account(tenant_id: int, account: str) -> dict:
    conn = db.connect()
    msg_sub = "SELECT id FROM messages WHERE tenant_id=? AND account=?"
    att_sub = f"SELECT id FROM attachments WHERE message_pk IN ({msg_sub})"
    p2 = (tenant_id, account)
    counts = {}
    counts["images"] = conn.execute(
        f"DELETE FROM images WHERE tenant_id=? AND attachment_id IN ({att_sub})",
        (tenant_id, *p2)).rowcount
    counts["drafts"] = conn.execute(
        f"DELETE FROM drafts WHERE tenant_id=? AND attachment_id IN ({att_sub})",
        (tenant_id, *p2)).rowcount
    counts["articles"] = conn.execute(
        f"DELETE FROM articles WHERE tenant_id=? AND attachment_id IN ({att_sub})",
        (tenant_id, *p2)).rowcount
    counts["attachments"] = conn.execute(
        f"DELETE FROM attachments WHERE tenant_id=? AND message_pk IN ({msg_sub})",
        (tenant_id, *p2)).rowcount
    counts["messages"] = conn.execute(
        "DELETE FROM messages WHERE tenant_id=? AND account=?", p2).rowcount
    counts["folder_state"] = conn.execute(
        "DELETE FROM folder_state WHERE tenant_id=? AND account=?", p2).rowcount
    conn.commit()
    conn.close()
    return counts


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    tid, acct = int(sys.argv[1]), sys.argv[2]
    res = reset_account(tid, acct)
    print(f"초기화 완료 (t{tid} / {acct}):")
    for k, v in res.items():
        print(f"  {k}: {v}건 삭제")
