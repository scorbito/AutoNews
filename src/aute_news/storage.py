"""파일 저장소 추상화 — 로컬 디스크 / Supabase Storage 교체 가능.

DB에는 파일 경로 대신 '키'(예: images/3/12/0.jpg)를 저장하고,
서빙·발행·재추출 시 storage.get(key) 로 바이트를 읽는다.
배포 시 STORAGE_BACKEND=supabase 로 하면 어디서 돌려도 파일이 유지된다.

env:
  STORAGE_BACKEND = local(기본) | supabase
  STORAGE_BUCKET  = files(기본)         # supabase 버킷명
  SUPABASE_URL, SUPABASE_SERVICE_KEY    # supabase 백엔드용
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    @abstractmethod
    def get(self, key: str) -> bytes | None: ...


class LocalStorage(Storage):
    """data/store/<key> 에 저장 (개발용)."""
    def __init__(self) -> None:
        self.base = ROOT / "data" / "store"

    def put(self, key, data, content_type="application/octet-stream") -> None:
        p = self.base / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key) -> bytes | None:
        p = self.base / key
        return p.read_bytes() if p.exists() else None


class SupabaseStorage(Storage):
    """Supabase Storage (S3 호환). service key 로 REST 호출."""
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_KEY", "")
        self.bucket = os.getenv("STORAGE_BUCKET", "files")
        if not self.url or not self.key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY 가 필요합니다.")

    def _h(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.key}", "apikey": self.key}
        if extra:
            h.update(extra)
        return h

    def ensure_bucket(self) -> None:
        requests.post(f"{self.url}/storage/v1/bucket", headers=self._h(),
                      json={"id": self.bucket, "name": self.bucket, "public": False},
                      timeout=20)  # 이미 있으면 409 — 무시

    def put(self, key, data, content_type="application/octet-stream") -> None:
        r = requests.post(
            f"{self.url}/storage/v1/object/{self.bucket}/{key}",
            headers=self._h({"Content-Type": content_type, "x-upsert": "true"}),
            data=data, timeout=60)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Storage put 실패({r.status_code}): {r.text[:150]}")

    def get(self, key) -> bytes | None:
        r = requests.get(f"{self.url}/storage/v1/object/{self.bucket}/{key}",
                         headers=self._h(), timeout=60)
        return r.content if r.status_code == 200 else None


_active: Storage | None = None


def get_storage() -> Storage:
    global _active
    if _active is None:
        _active = SupabaseStorage() if os.getenv("STORAGE_BACKEND", "local").lower() == "supabase" \
            else LocalStorage()
    return _active


def mime_for(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "pdf": "application/pdf", "html": "text/html"}.get(
                ext, "application/octet-stream")
