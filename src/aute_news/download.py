"""LINK_BASED 다운로드 (이식명세 §5) — 본문 다운로드 링크를 받아 첨부 파일화.

kmmailer.korea.kr(정부 메일러), Google Drive 등에서 보도자료 파일을 내려받아
data/downloads/ 에 저장하고, 추출 파이프라인에 태울 수 있게 경로/파일명을 돌려준다.
서버별 특수처리가 필요하면 여기서 분기(현재는 일반 HTTP + 파일명 추정).
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

DL_DIR = Path(__file__).resolve().parents[2] / "data" / "downloads"
UA = {"User-Agent": "Mozilla/5.0 aute_news"}

_EXT_FROM_CT = {
    "application/pdf": "pdf",
    "application/haansofthwp": "hwp",
    "application/x-hwp": "hwp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/zip": "zip",
    "text/plain": "txt",
}


def _filename_from(resp: requests.Response, url: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    if m:
        return unquote(m.group(1)).strip()
    name = Path(urlparse(url).path).name
    if name and "." in name:
        return unquote(name)
    ext = _EXT_FROM_CT.get(resp.headers.get("Content-Type", "").split(";")[0].strip(), "")
    return f"download.{ext}" if ext else "download.bin"


def download_link(url: str, dest_dir: Path | None = None) -> dict | None:
    """URL → 파일 저장. 반환 {filename, path, size, content_type} 또는 None."""
    dest_dir = dest_dir or DL_DIR
    try:
        r = requests.get(url, headers=UA, timeout=60, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException:
        return None
    if len(r.content) < 256:                       # 너무 작으면 실패/리다이렉트 페이지
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = _filename_from(r, url)
    safe = "".join(c if c.isalnum() or c in "._-가-힣" else "_" for c in filename)
    path = dest_dir / safe
    path.write_bytes(r.content)
    return {"filename": filename, "path": str(path), "size": len(r.content),
            "content_type": r.headers.get("Content-Type", "")}
