"""배포 1회용 — Supabase Storage 버킷 생성(이미 있으면 무시).

  STORAGE_BACKEND=supabase 환경에서:  python scripts/init_storage.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import os  # noqa: E402

os.environ.setdefault("STORAGE_BACKEND", "supabase")

from aute_news.storage import SupabaseStorage  # noqa: E402


def main() -> None:
    s = SupabaseStorage()
    s.ensure_bucket()
    print(f"버킷 '{s.bucket}' 준비 완료 (이미 있으면 그대로).")


if __name__ == "__main__":
    main()
