"""기자 검토 UI 실행.

  python scripts/serve.py
  → http://127.0.0.1:8000 접속
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("aute_news.web.app:app", host="127.0.0.1", port=8000, reload=False)
