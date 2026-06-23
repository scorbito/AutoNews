"""기자 검토 UI 실행.

  python scripts/serve.py
  → http://127.0.0.1:8000 접속
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    # reload=True: 코드 수정 시 자동 재시작(개발 편의). 운영에선 False 권장.
    uvicorn.run("aute_news.web.app:app", host="127.0.0.1", port=8000,
                reload=True, reload_dirs=["src"])
