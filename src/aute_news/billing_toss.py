"""토스페이먼츠 빌링(정기결제) API 래퍼.

흐름(SDK 카드등록 방식 — 우리 서버는 카드번호를 만지지 않음):
  1) 프론트: TossPayments(clientKey).requestBillingAuth('카드', {customerKey, successUrl, failUrl})
  2) 성공 → successUrl?authKey=...&customerKey=...
  3) 서버: issue_billing_key(authKey, customerKey) → billingKey
  4) 청구: charge(billingKey, customerKey, amount, orderId, orderName)

인증: Authorization: Basic base64(secretKey + ":")  ← 콜론 필수.
테스트 키(test_*)면 실제 승인 없이 가상 처리된다.
"""
from __future__ import annotations

import base64
import os

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.tosspayments.com"


def client_key() -> str:
    return os.getenv("TOSS_CLIENT_KEY", "")


def _secret_key() -> str:
    sk = os.getenv("TOSS_SECRET_KEY", "")
    if not sk:
        raise RuntimeError("TOSS_SECRET_KEY 가 .env 에 없습니다.")
    return sk


def is_test_mode() -> bool:
    return _secret_key().startswith("test_")


def _auth_header() -> dict:
    token = base64.b64encode(f"{_secret_key()}:".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


class TossError(RuntimeError):
    """토스 API 오류(코드·메시지 포함)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _post(path: str, body: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=_auth_header(), json=body, timeout=20)
    data = r.json() if r.content else {}
    if r.status_code != 200:
        raise TossError(data.get("code", str(r.status_code)),
                        data.get("message", r.text[:200]))
    return data


def issue_billing_key(auth_key: str, customer_key: str) -> dict:
    """authKey → 빌링키 발급. 반환 dict 에 billingKey, card 정보 등 포함."""
    return _post("/v1/billing/authorizations/issue",
                 {"authKey": auth_key, "customerKey": customer_key})


def charge(billing_key: str, customer_key: str, amount: int, order_id: str,
           order_name: str) -> dict:
    """빌링키로 결제 승인(청구). 성공 시 결제 객체 반환, 실패 시 TossError."""
    return _post(f"/v1/billing/{billing_key}",
                 {"customerKey": customer_key, "amount": amount,
                  "orderId": order_id, "orderName": order_name})
