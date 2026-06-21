"""예약 수집 — 테넌트별 수집 시간(KST)에 맞춰 수집+처리.

외부 스케줄러(Windows 작업 스케줄러 / cron)가 run_scheduled.py 를 주기적으로
호출하면, 그 시점에 '수집할 시간'인 테넌트만 골라 실행한다.
collect_times: "HH:MM" 쉼표 목록(KST). window 분 안에 들면 due.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import admin, db
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
            t = t.strip()
            if not t:
                continue
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
        collect = collect_for_tenant(tid)
        conn = db.connect()
        made = admin.process_tenant(conn, tid)
        conn.close()
        results.append({"tenant_id": tid, "collect": collect, "articles_made": made})
    return results
