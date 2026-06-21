"""테넌트(신문사) 설정 입력 (관리자용, 매니지드 온보딩).

  python scripts/set_config.py <tenant_id> key=value [key=value ...]

설정 키:
  메일:   imap_host, imap_email, imap_password, imap_folders
  발행:   publisher(html|atpaju), ndsoft_base_url, cms_user, cms_password,
          cms_user_name, cms_user_email, cms_section, pipeline_mode(review|auto)

예)
  python scripts/set_config.py 3 imap_host=imap.naver.com imap_email=a@naver.com \
      imap_password=앱비번 imap_folders=내게쓴메일함 \
      publisher=atpaju ndsoft_base_url=https://www.atpaju.com \
      cms_user=evahoba cms_password=비번 cms_section=S1N4 pipeline_mode=review

비밀번호(imap_password, cms_password)는 암호화 저장됩니다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aute_news import db  # noqa: E402

PW_KEYS = {"imap_password", "cms_password"}


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return
    tenant_id = int(sys.argv[1])
    kw = {}
    for pair in sys.argv[2:]:
        if "=" not in pair:
            print(f"무시(형식오류): {pair}")
            continue
        k, v = pair.split("=", 1)
        kw[k.strip()] = v
    conn = db.connect()
    if not conn.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone():
        print(f"테넌트 {tenant_id} 없음.")
        return
    db.set_tenant_config(conn, tenant_id, **kw)
    cfg = db.get_tenant_config(conn, tenant_id)
    conn.close()
    safe = {k: ("***" if k in ("imap_password", "cms_password") and v else v)
            for k, v in cfg.items()}
    print(f"테넌트 {tenant_id} 설정 저장됨:")
    for k, v in safe.items():
        if v not in (None, ""):
            print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
