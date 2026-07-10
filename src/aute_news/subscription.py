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

# 플랜 2종. 2026년 내 가입자는 launch_price 로 평생 고정(pricing_tier='launch2026'),
# 이후 가입자는 list_price(정가). 티어는 첫 결제 때 subscriptions.pricing_tier 에 저장돼 잠긴다.
PLANS = {
    "basic": {"label": "베이직", "quota": 600,  "list_price": 99000,  "launch_price": 59000},
    "pro":   {"label": "프로",   "quota": 1000, "list_price": 149000, "launch_price": 89000},
}
LAUNCH_LAST_YEAR = 2026      # 이 해까지 가입하면 launch_price 평생 고정

DEFAULT_QUOTA = PLANS["basic"]["quota"]   # 결제주기당 기사 생성 한도(베이직 기준)
PERIOD_DAYS = 30             # 1달 구독 주기
TRIAL_DAYS = 14             # 가입 시 무료 체험 기간

BANK_TRANSFER_DISCOUNT_RATE = 0.05

ACTIVE_STATUSES = ("active", "trialing")


def order_name(plan: str) -> str:
    return f"뉴스플로우 AI {PLANS.get(plan, PLANS['basic'])['label']} 플랜"


def pricing_tier(conn, tenant_id: int) -> str:
    """테넌트 가격 티어. 구독에 저장된 값 우선(고정), 없으면 가입(테넌트 생성) 연도로 판정."""
    sub = db.get_subscription(conn, tenant_id)
    if sub and sub.get("pricing_tier"):
        return sub["pricing_tier"]
    row = conn.execute("SELECT created_at FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    year = row["created_at"].year if row and row.get("created_at") else datetime.now(KST).year
    return "launch2026" if year <= LAUNCH_LAST_YEAR else "standard"


def plan_price(plan: str, tier: str) -> int:
    """플랜·티어 → 월 청구액(원)."""
    p = PLANS.get(plan) or PLANS["basic"]
    return p["launch_price"] if tier == "launch2026" else p["list_price"]


def bank_transfer_amount(price: int) -> int:
    """무통장입금 권장 금액 — 카드 결제액에서 5% 할인 후 1,000원 단위 내림."""
    discounted = int(price * (1 - BANK_TRANSFER_DISCOUNT_RATE))
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


def activate(conn, tenant_id: int, *, days: int = PERIOD_DAYS, quota: int | None = None,
             plan: str = "basic", status: str = "active") -> None:
    """구독 활성화/연장(결제 성공 콜백·관리자 수동이 호출). quota 미지정 시 플랜 기본값.

    비활성→활성 전환이면 folder_state 를 리셋해 '재구독일 0시부터' 다시 수집(옛 메일 스킵).
    """
    if quota is None:
        quota = PLANS.get(plan, PLANS["basic"])["quota"]
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


def subscribe_with_auth(conn, tenant_id: int, auth_key: str, customer_key: str,
                        plan: str = "basic") -> dict:
    """카드 등록 성공 콜백 처리 — 빌링키 발급 → 첫 결제 → 구독 활성화.

    가격 티어(2026 고정가/정가)는 이 시점에 subscriptions.pricing_tier 로 잠긴다.
    실패 시 billing_toss.TossError 가 그대로 전파된다(호출부에서 처리)."""
    if plan not in PLANS:
        plan = "basic"
    tier = pricing_tier(conn, tenant_id)
    res = billing_toss.issue_billing_key(auth_key, customer_key)
    billing_key = res["billingKey"]
    db.save_billing_key(conn, tenant_id, billing_key, customer_key)
    amount = plan_price(plan, tier)
    order_id = f"nf-{tenant_id}-{secrets.token_hex(8)}"
    billing_toss.charge(billing_key, customer_key, amount, order_id, order_name(plan))
    db.record_charge(conn, tenant_id)
    db.upsert_subscription(conn, tenant_id, pricing_tier=tier)
    activate(conn, tenant_id, plan=plan)          # 유료 30일 시작
    return {"amount": amount, "plan": plan}


def change_plan(conn, tenant_id: int, plan: str) -> dict | None:
    """플랜 변경(업/다운) — 저장된 빌링키로 즉시 결제하고 오늘부터 새 30일 주기 시작.

    빌링키가 없으면 None(카드 등록부터 필요). 실패 시 TossError 전파."""
    if plan not in PLANS:
        raise ValueError(f"unknown plan: {plan}")
    sub = db.get_subscription(conn, tenant_id) or {}
    billing_key = db.get_billing_key(conn, tenant_id)
    customer_key = sub.get("customer_key")
    if not billing_key or not customer_key:
        return None
    tier = pricing_tier(conn, tenant_id)
    amount = plan_price(plan, tier)
    order_id = f"nf-{tenant_id}-{secrets.token_hex(8)}"
    billing_toss.charge(billing_key, customer_key, amount, order_id, order_name(plan))
    db.record_charge(conn, tenant_id)
    db.upsert_subscription(conn, tenant_id, pricing_tier=tier)
    activate(conn, tenant_id, plan=plan)          # 오늘부터 새 주기(한도 리셋)
    return {"amount": amount, "plan": plan}


def charge_renewal(conn, tenant_id: int) -> bool:
    """정기 자동청구(cron) — 저장된 빌링키로 청구. 성공 시 30일 연장."""
    sub = db.get_subscription(conn, tenant_id)
    if not sub:
        return False
    billing_key = db.get_billing_key(conn, tenant_id)
    customer_key = sub.get("customer_key")
    if not billing_key or not customer_key:
        return False
    plan = sub.get("plan") if sub.get("plan") in PLANS else "basic"
    tier = sub.get("pricing_tier") or pricing_tier(conn, tenant_id)
    amount = plan_price(plan, tier)
    order_id = f"nf-{tenant_id}-{secrets.token_hex(8)}"
    try:
        billing_toss.charge(billing_key, customer_key, amount, order_id, order_name(plan))
    except billing_toss.TossError:
        db.upsert_subscription(conn, tenant_id, status="past_due")
        return False
    db.record_charge(conn, tenant_id)
    db.upsert_subscription(conn, tenant_id, pricing_tier=tier)
    activate(conn, tenant_id, plan=plan)          # 다음 30일로 연장
    return True


def due_for_renewal(conn) -> list[int]:
    """갱신 청구 대상 — 활성(active)이고 빌링키 있고 만료가 지난/임박한 테넌트 id."""
    rows = conn.execute(
        "SELECT tenant_id FROM subscriptions "
        "WHERE status='active' AND billing_key_enc IS NOT NULL "
        "AND period_end IS NOT NULL AND period_end <= now()").fetchall()
    return [r["tenant_id"] for r in rows]
