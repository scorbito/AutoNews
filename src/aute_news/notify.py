"""실패 알림·로깅 — 무인 자동화의 '조용한 실패'를 잡는다.

- log_error: 항상 stderr 에 기록(+ 예외 트레이스백).
- alert_operator: 운영자 텔레그램 알림(환경변수 설정 시). 같은 키는 throttle 초 내 1회만.
- report_failure: 로깅 + 운영자 알림을 한 번에.

환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (둘 다 있어야 텔레그램 전송).
없으면 stderr 로깅만 — 코드 어디서 호출해도 안전.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

import requests

_last_sent: dict[str, float] = {}     # dedup: key → 마지막 전송 시각(프로세스 메모리)


def log_error(stage: str, detail: str, tenant_id: int | None = None,
              exc: BaseException | None = None) -> None:
    """항상 stderr 로 기록. 운영 로그(Railway)에서 검색 가능."""
    head = f"[ERROR] {stage}"
    if tenant_id is not None:
        head += f" tenant={tenant_id}"
    print(f"{head} — {detail}", file=sys.stderr, flush=True)
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


def _send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=10)
    except Exception:  # noqa: BLE001 (알림 실패가 본 작업을 막지 않게)
        pass


def alert_operator(text: str, dedup_key: str | None = None, throttle_sec: int = 3600) -> None:
    """운영자 텔레그램 알림. dedup_key 가 있으면 throttle_sec 내 같은 키는 생략(스팸 방지)."""
    if dedup_key:
        now = time.time()
        if now - _last_sent.get(dedup_key, 0.0) < throttle_sec:
            return
        _last_sent[dedup_key] = now
    _send_telegram(text)


def report_failure(stage: str, tenant_id: int | None = None,
                   exc: BaseException | None = None, detail: str | None = None) -> None:
    """실패 1건 처리 — stderr 로깅 + 운영자 알림. cron·작업큐·발행 등 어디서든 호출."""
    msg = detail or (f"{type(exc).__name__}: {exc}" if exc else stage)
    log_error(stage, msg, tenant_id, exc)
    where = f" (tenant {tenant_id})" if tenant_id is not None else ""
    alert_operator(f"⚠️ 실패: {stage}{where}\n{msg}", dedup_key=f"{stage}:{tenant_id}")
