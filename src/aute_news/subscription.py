"""구독 게이트 — 수집·기사생성을 구독상태/한도로 통제.

핵심 규칙(단일 게이트):
  can_use = 구독활성(active/trialing, 만료 전) AND 남은 한도 > 0
비용이 드는 행동(메일 수집·기사 생성)은 can_use 가 True 일 때만 허용한다.
한도를 못 쓰면 수집도 막는다 — 받아둬도 보관 7일 후 삭제될 뿐(데이터 손실)이라.
발행·검토·열람은 비용이 없으므로 항상 허용(여기서 막지 않음).

결제(토스 빌링)는 아직 없음 — activate()/extend() 로 수동 활성화(결제 자리 대체).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import db

KST = timezone(timedelta(hours=9))

# 기본 플랜 값(결제 붙기 전 단일 플랜)
DEFAULT_QUOTA = 400          # 결제주기당 기사 생성 한도
PERIOD_DAYS = 30             # 1달 구독 주기
TRIAL_DAYS = 14             # 가입 시 무료 체험 기간

ACTIVE_STATUSES = ("active", "trialing")


def status_view(conn, tenant_id: int) -> dict:
    """구독 상태 요약(화면·게이트 공용).

    반환: {exists, status, plan, quota, used, remaining, period_start, period_end,
           active, blocked_reason}
    blocked_reason: None | 'inactive' | 'quota' (막힌 이유)
    """
    sub = db.get_subscription(conn, tenant_id)
    if not sub:
        return {"exists": False, "status": "inactive", "plan": None,
                "quota": 0, "used": 0, "remaining": 0,
                "period_start": None, "period_end": None,
                "active": False, "blocked_reason": "inactive"}
    quota = sub.get("monthly_quota") or 0
    used = db.period_article_count(conn, tenant_id, sub.get("period_start"))
    remaining = max(0, quota - used)
    active_state = sub["status"] in ACTIVE_STATUSES and not sub.get("expired")
    if not active_state:
        reason = "inactive"
    elif remaining <= 0:
        reason = "quota"
    else:
        reason = None
    return {"exists": True, "status": sub["status"], "plan": sub.get("plan"),
            "quota": quota, "used": used, "remaining": remaining,
            "period_start": sub.get("period_start"), "period_end": sub.get("period_end"),
            "active": active_state, "blocked_reason": reason}


def can_use(conn, tenant_id: int) -> tuple[bool, str | None]:
    """수집·생성 허용 여부 + 막힌 이유. (True, None) 이면 허용."""
    v = status_view(conn, tenant_id)
    return (v["blocked_reason"] is None, v["blocked_reason"])


def block_message(reason: str | None) -> str:
    """막힌 이유 → 사용자 안내 문구."""
    if reason == "quota":
        return "이번 결제주기 기사 한도를 모두 사용했습니다. 다음 결제일에 초기화됩니다."
    if reason == "inactive":
        return "구독이 필요합니다. 결제(준비 중) 후 메일 수집·기사 생성이 가능합니다."
    return ""


def activate(conn, tenant_id: int, *, days: int = PERIOD_DAYS, quota: int = DEFAULT_QUOTA,
             plan: str = "basic", status: str = "active") -> None:
    """구독 활성화/연장(수동 — 나중에 결제 성공 콜백이 이걸 호출).

    비활성→활성 전환이면 folder_state 를 리셋해 '재구독일 0시부터' 다시 수집(옛 메일 스킵).
    """
    prev = db.get_subscription(conn, tenant_id)
    was_active = bool(prev) and prev["status"] in ACTIVE_STATUSES and not prev.get("expired")
    now = datetime.now(KST)
    db.upsert_subscription(
        conn, tenant_id, status=status, plan=plan, monthly_quota=quota,
        period_start=now, period_end=now + timedelta(days=days))
    if not was_active:
        # 재구독(또는 첫 활성) — 공백기 묵은 메일은 스킵하고 오늘부터 새로 수집
        db.reset_folder_state(conn, tenant_id)


def start_trial(conn, tenant_id: int, *, days: int = TRIAL_DAYS, quota: int = DEFAULT_QUOTA) -> None:
    """가입 직후 무료 체험 시작."""
    activate(conn, tenant_id, days=days, quota=quota, status="trialing")


def deactivate(conn, tenant_id: int) -> None:
    """구독 비활성화(만료 처리) — 자동·수집·생성 즉시 정지."""
    db.upsert_subscription(conn, tenant_id, status="canceled")
