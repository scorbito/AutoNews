"""예약 수집 — 테넌트별 수집 시간(KST)에 맞춰 수집+처리.

외부 스케줄러(Windows 작업 스케줄러 / cron)가 run_scheduled.py 를 주기적으로
호출하면, 그 시점에 '수집할 시간'인 테넌트만 골라 실행한다.
collect_times: "HH:MM" 쉼표 목록(KST). window 분 안에 들면 due.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import admin, db, notify
from .collector import collect_for_tenant

KST = timezone(timedelta(hours=9))


def _now_minutes() -> int:
    now = datetime.now(KST)
    return now.hour * 60 + now.minute


def due_tenants(conn, window_min: int = 5) -> list[int]:
    """지금(KST) 기준 window 분 내에 수집예약이 걸린 테넌트 id 목록."""
    now_min = _now_minutes()
    rows = conn.execute(
        "SELECT tenant_id, collect_times FROM tenant_config "
        "WHERE collect_enabled=1 AND collect_times IS NOT NULL AND collect_times <> ''"
    ).fetchall()
    due = []
    for r in rows:
        for t in (r["collect_times"] or "").split(","):
            t = t.strip().lower()
            if not t:
                continue
            # 간격 예약: "30m" 또는 "*/30" = 30분마다
            iv = None
            if t.endswith("m") and t[:-1].isdigit():
                iv = int(t[:-1])
            elif t.startswith("*/") and t[2:].isdigit():
                iv = int(t[2:])
            if iv:
                if iv > 0 and (now_min % iv) < window_min:
                    due.append(r["tenant_id"])
                    break
                continue
            # 특정시각: "HH:MM"
            try:
                hh, mm = (int(x) for x in t.split(":"))
            except ValueError:
                continue
            if 0 <= (now_min - (hh * 60 + mm)) < window_min:
                due.append(r["tenant_id"])
                break
    return due


def run_due(window_min: int = 5) -> list[dict]:
    """예약 시간이 된 테넌트들을 수집 + 파이프라인 처리."""
    conn = db.connect()
    ids = due_tenants(conn, window_min)
    conn.close()
    results = []
    for tid in ids:
        try:
            collect = collect_for_tenant(tid)   # 자동 모드 테넌트의 메일 계정 전체 수집
            conn = db.connect()
            made = admin.process_tenant(conn, tid)
            conn.close()
            results.append({"tenant_id": tid, "collect": collect, "articles_made": made})
        except Exception as e:  # noqa: BLE001 (한 테넌트 실패가 다른 테넌트·cron을 멈추지 않게)
            notify.report_failure("예약 수집/처리", tid, exc=e)
            results.append({"tenant_id": tid, "error": type(e).__name__})
    return results
