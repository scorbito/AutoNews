"""Gemini 기사 초안 생성기.

추출된 원문(ArticleDraft.body_text)을 받아 구조화된 기사 초안을 생성한다.
- google-genai 통합 SDK 사용, response_schema 로 출력 구조 강제(JSON).
- 환각 방지: 원문에 없는 사실/수치/인용 추가 금지를 시스템 지시에 강하게 명시.
- 저작권: 그대로 복붙이 아니라 재작성(리라이팅).
최종 결과는 '초안' — 기자 검토/수정을 전제로 한다.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"   # 품질 우선 시 "gemini-2.5-pro"

SYSTEM_INSTRUCTION = """\
당신은 한국어 뉴스 매체의 숙련된 기자다. 아래에 주어지는 '원문'(보도자료/공문 등에서 추출한 텍스트)을
신문 기사체로 정리해 기사 초안을 작성한다. 반드시 다음 원칙을 지킨다.

1) 사실 보존: 원문에 명시된 사실(일시·장소·인물·수치·명칭)만 사용한다.
   원문에 없는 정보, 추측, 배경지식, 과장은 절대 추가하지 않는다. 불확실하면 쓰지 않는다.
2) 5W1H: 누가/언제/어디서/무엇을/왜/어떻게가 드러나게 구성한다.
3) 재작성: 원문 문장을 그대로 복사하지 말고 기사체로 다시 쓴다(저작권).
4) 문체: 객관적·간결한 '~다' 종결의 신문 기사체. 추출 과정의 표/줄바꿈 잡음은 정리한다.
5) 이것은 '초안'이며 기자가 검토·수정한다. 사실 확인이 필요한 부분은 그대로 두되 지어내지 않는다.
"""


class NewsArticle(BaseModel):
    headline: str = Field(description="기사 제목 (간결한 한 줄)")
    subheadline: str = Field(description="부제 (제목 보완, 한 줄)")
    lead: str = Field(description="리드문 (핵심을 요약한 첫 문단)")
    body: str = Field(description="본문 (여러 문단, 문단 구분은 빈 줄)")
    summary: list[str] = Field(description="3줄 요약 (각 항목 한 문장)")
    tags: list[str] = Field(description="키워드 태그 3~6개")
    confidence_notes: str = Field(
        description="원문 정보가 부족해 기자 확인이 필요한 부분 메모(없으면 빈 문자열)"
    )


_CLIENT: genai.Client | None = None


def _client() -> genai.Client:
    # 모듈 레벨에 보관(싱글톤). 임시객체로 만들면 GC가 httpx 연결을 닫아
    # 'client has been closed' 에러가 난다.
    global _CLIENT
    if _CLIENT is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY 가 .env 에 없습니다. Google AI Studio 에서 발급하세요.")
        _CLIENT = genai.Client(api_key=key)
    return _CLIENT


def generate_article(
    body_text: str,
    source_title: str | None = None,
    source_note: str | None = None,
    model: str = DEFAULT_MODEL,
) -> NewsArticle:
    """원문 텍스트 → 구조화된 기사 초안(NewsArticle)."""
    prompt = "[원문]\n"
    if source_title:
        prompt += f"(원문 제목 후보: {source_title})\n"
    prompt += body_text.strip()
    if source_note:
        prompt += f"\n\n[출처 메모] {source_note}"

    resp = _client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=NewsArticle,
            temperature=0.3,
        ),
    )
    return resp.parsed  # type: ignore[return-value]


class ImageKind(BaseModel):
    kind: str = Field(description="photo(뉴스용 사진) | stamp(직인/도장) | logo(로고) | "
                                  "diagram(표/도표/서식) | unknown 중 하나")
    is_article_photo: bool = Field(description="기사에 실을 만한 실제 사진이면 true")
    caption: str = Field(description="사진이면 한 줄 설명(아니면 빈 문자열)")


def classify_image(data: bytes, ext: str, model: str = DEFAULT_MODEL) -> ImageKind:
    """Gemini 멀티모달로 이미지가 기사용 사진인지/직인·로고인지 분류."""
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    resp = _client().models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=data, mime_type=mime),
            "이 이미지를 분류하라. 직인/도장/로고/서식표인지, 아니면 기사에 실을 사진인지 판단하라.",
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ImageKind,
            temperature=0.0,
        ),
    )
    return resp.parsed  # type: ignore[return-value]


def render_markdown(a: NewsArticle) -> str:
    """NewsArticle → 편집 가능한 마크다운 텍스트."""
    lines = [f"# {a.headline}", f"## {a.subheadline}", "", a.lead, "", a.body, "",
             "■ 3줄 요약", *[f"- {x}" for x in a.summary], "",
             f"■ 태그: {', '.join(a.tags)}"]
    if a.confidence_notes.strip():
        lines += ["", f"■ [기자 확인 필요] {a.confidence_notes}"]
    return "\n".join(lines)
