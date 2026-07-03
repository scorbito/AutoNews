"""구독 게이트 — 수집·기사생성을 구독상태/한도로 통제.

핵심 규칙(단일 게이트):
  can_use = 구독활성(active/trialing, 만료 전) AND 남은 한도 > 0
비용이 드는 행동(메일 수집·기사 생성)은 can_use 가 True 일 때만 허용한다.
한도를 못 쓰면 수집도 막는다 — 받아둬도 보관 7일 후 삭제될 뿐(데이터 손실)이라.
발행·검토·열람은 비용이 없으므로 항상 허용(여기서 막지 않음).

결제(토스 빌링)는 아직 없음 — activate()/extend() 로 수동 활성화(결제 자리 대체).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from . import billing_toss, db

KST = timezone(timedelta(hours=9))
ORDER_NAME = "뉴스플로우 AI 구독"

# 기본 플랜 값(단일 플랜)
DEFAULT_QUOTA = 400          # 결제주기당 기사 생성 한도
PERIOD_DAYS = 30             # 1달 구독 주기
TRIAL_DAYS = 14             # 가입 시 무료 체험 기간

# 가격(원) — 첫 결제 달은 반값, 이후 정상가
MONTHLY_PRICE = 99000
FIRST_MONTH_PRICE = 49500
BANK_TRANSFER_DISCOUNT_RATE = 0.05

ACTIVE_STATUSES = ("active", "trialing")


def charge_amount(charges_count: int) -> int:
    """이번에 청구할 금액. 첫 결제(charges_count==0)는 반값, 이후 정상가."""
    return FIRST_MONTH_PRICE if (charges_count or 0) <= 0 else MONTHLY_PRICE


def bank_transfer_amount(charges_count: int) -> int:
    """무통장입금 권장 금액. 카드 결제액에서 5% 할인 후 1,000원 단위로 내림."""
    amount = charge_amount(charges_count)
    discounted = int(amount * (1 - BANK_TRANSFER_DISCOUNT_RATE))
    return max(0, discounted // 1000 * 1000)


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


# --- 토스 빌링(정기결제) ---

def ensure_customer_key(conn, tenant_id: int) -> str:
    """토스 구매자 고유 ID. 없으면 생성·저장(프론트 결제창에 넘김)."""
    sub = db.get_subscription(conn, tenant_id)
    if sub and sub.get("customer_key"):
        return sub["customer_key"]
    ck = "nf_" + secrets.token_hex(16)
    db.upsert_subscription(conn, tenant_id, customer_key=ck)
    return ck


def subscribe_with_auth(conn, tenant_id: int, auth_key: str, customer_key: str) -> dict:
    """카드 등록 성공 콜백 처리 — 빌링키 발급 → 첫 결제(첫 달 반값) → 구독 활성화.

    실패 시 billing_toss.TossError 가 그대로 전파된다(호출부에서 처리)."""
    res = billing_toss.issue_billing_key(auth_key, customer_key)
    billing_key = res["billingKey"]
    db.save_billing_key(conn, tenant_id, billing_key, customer_key)
    sub = db.get_subscription(conn, tenant_id) or {}
    amount = charge_amount(sub.get("charges_count", 0))
    order_id = f"nf-{tenant_id}-{secrets.token_hex(8)}"
    billing_toss.charge(billing_key, customer_key, amount, order_id, ORDER_NAME)
    db.record_charge(conn, tenant_id)
    activate(conn, tenant_id)           # 유료 30일 시작
    return {"amount": amount}


def charge_renewal(conn, tenant_id: int) -> bool:
    """정기 자동청구(cron) — 저장된 빌링키로 청구. 성공 시 30일 연장."""
    sub = db.get_subscription(conn, tenant_id)
    if not sub:
        return False
    billing_key = db.get_billing_key(conn, tenant_id)
    customer_key = sub.get("customer_key")
    if not billing_key or not customer_key:
        return False
    amount = charge_amount(sub.get("charges_count", 0))
    order_id = f"nf-{tenant_id}-{secrets.token_hex(8)}"
    try:
        billing_toss.charge(billing_key, customer_key, amount, order_id, ORDER_NAME)
    except billing_toss.TossError:
        db.upsert_subscription(conn, tenant_id, status="past_due")
        return False
    db.record_charge(conn, tenant_id)
    activate(conn, tenant_id)           # 다음 30일로 연장
    return True


def due_for_renewal(conn) -> list[int]:
    """갱신 청구 대상 — 활성(active)이고 빌링키 있고 만료가 지난/임박한 테넌트 id."""
    rows = conn.execute(
        "SELECT tenant_id FROM subscriptions "
        "WHERE status='active' AND billing_key_enc IS NOT NULL "
        "AND period_end IS NOT NULL AND period_end <= now()").fetchall()
    return [r["tenant_id"] for r in rows]
