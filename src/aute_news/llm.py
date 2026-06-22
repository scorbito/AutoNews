"""LLM 추상화 (이식명세 §4) — 교체 가능한 provider + 프롬프트 로더.

3개 AI 단계(Triage/Split/Generate)와 Vision 이 모두 이 인터페이스를 쓴다.
기본은 Gemini. JSON 출력은 코드펜스 제거 + {..} 추출 + 정규식 복구 폴백을 갖춘다.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_MODEL = "gemini-2.5-flash"


# ── 프롬프트 로더 ─────────────────────────────────────────────
def load_prompt(stage: str) -> tuple[str, str]:
    """prompt_<stage>.txt → (system, user_template). stage ∈ Triage/Split/Generate."""
    text = (PROMPT_DIR / f"prompt_{stage}.txt").read_text(encoding="utf-8")
    user, system = "", text
    if "===SYSTEM MESSAGE===" in text:
        head, system = text.split("===SYSTEM MESSAGE===", 1)
        if "===USER MESSAGE===" in head:
            user = head.split("===USER MESSAGE===", 1)[1]
    return system.strip(), user.strip()


# ── JSON 복구 ────────────────────────────────────────────────
def parse_json_loose(raw: str) -> dict:
    """순수 JSON 가정 + 실패 시 코드펜스 제거·{..} 추출로 복구."""
    if not raw:
        raise ValueError("빈 응답")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    s = re.sub(r"```json\s*|\s*```", "", raw).strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1:
        s = s[a:b + 1]
    return json.loads(s)


# ── Provider 인터페이스 ──────────────────────────────────────
class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str, *, temperature: float = 0.2) -> dict:
        ...

    @abstractmethod
    def complete_text(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        ...

    def vision_json(self, system: str, user: str, image: bytes, ext: str,
                    *, temperature: float = 0.0) -> dict:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    _client = None  # 싱글톤(임시객체면 GC가 httpx 닫음 → 'client has been closed')

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model

    def _c(self):
        if GeminiProvider._client is None:
            from google import genai
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY 가 .env 에 없습니다.")
            GeminiProvider._client = genai.Client(api_key=key)
        return GeminiProvider._client

    def complete_json(self, system: str, user: str, *, temperature: float = 0.2) -> dict:
        from google.genai import types
        resp = self._c().models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json",
                temperature=temperature))
        return parse_json_loose(resp.text)

    def complete_text(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        from google.genai import types
        resp = self._c().models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=temperature))
        return resp.text or ""

    def vision_json(self, system: str, user: str, image: bytes, ext: str,
                    *, temperature: float = 0.0) -> dict:
        from google.genai import types
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        resp = self._c().models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image, mime_type=mime), user],
            config=types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json",
                temperature=temperature))
        return parse_json_loose(resp.text)


def get_llm(model: str | None = None) -> LLMProvider:
    """활성 LLM. LLM_PROVIDER 로 교체(기본 gemini).

    model 을 명시하면 그 모델을 쓰고, 없으면 LLM_MODEL env(기본 DEFAULT_MODEL).
    단계별로 다른 모델을 쓰려면 호출부에서 model 을 넘긴다(예: 기사 생성=flash).
    """
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        return GeminiProvider(model or os.getenv("LLM_MODEL", DEFAULT_MODEL))
    raise RuntimeError(f"미지원 LLM_PROVIDER: {provider}")
