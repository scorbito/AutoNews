"""신문사 계정 생성 (관리자용, 매니지드 온보딩).

Supabase Auth 사용자 생성 + 테넌트(신문사) 생성 + 매핑.

  python scripts/create_account.py "신문사이름" 로그인이메일 비밀번호 [기존tenant_id]

.env 필요: SUPABASE_URL, SUPABASE_SERVICE_KEY (service_role, 비밀!)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from aute_news import db  # noqa: E402

URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE = os.getenv("SUPABASE_SERVICE_KEY", "")


def main() -> None:
    if len(sys.argv) < 4:
        print('사용법: python scripts/create_account.py "신문사명" 이메일 비밀번호 [tenant_id]')
        return
    name, email, password = sys.argv[1], sys.argv[2], sys.argv[3]
    if not URL or not SERVICE:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 필요합니다.")
        return

    conn = db.connect()
    # 1) 테넌트
    if len(sys.argv) > 4:
        tid = int(sys.argv[4])
    else:
        tid = conn.execute(
            "INSERT INTO tenants (name) VALUES (?) RETURNING id", (name,)).fetchone()["id"]
        print(f"테넌트 생성: id={tid} ({name})")

    # 2) Supabase Auth 사용자 생성
    r = requests.post(
        f"{URL}/auth/v1/admin/users",
        headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}",
                 "Content-Type": "application/json"},
        json={"email": email, "password": password, "email_confirm": True}, timeout=20)
    if r.status_code not in (200, 201):
        print(f"사용자 생성 실패({r.status_code}): {r.text[:200]}")
        conn.close()
        return
    uid = r.json()["id"]
    print(f"Auth 사용자 생성: {email} ({uid})")

    # 3) 매핑
    conn.execute(
        """INSERT INTO tenant_users (user_id, tenant_id, email, role) VALUES (?,?,?,'editor')
           ON CONFLICT (user_id) DO UPDATE SET tenant_id=excluded.tenant_id""",
        (uid, tid, email))
    conn.close()
    print(f"완료: {email} → 테넌트 {tid} ({name}) 로그인 가능")


if __name__ == "__main__":
    main()
