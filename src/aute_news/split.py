"""Split (이식명세 §4) — 한 보도자료 문서 안의 N건을 기사 N개로 분리.

⚠️ '분할'만 한다. 새 문장 작성·요약·재편집 금지(그건 Generate 단계).
press_release_text 가 부실하면 articles=[] 로 반환(수동 처리 신호).
"""
from __future__ import annotations

from .llm import get_llm, load_prompt


def run_split(press_release_text: str, *, subject: str = "", from_name: str = "",
              from_address: str = "", primary_org: str = "",
              article_count_estimate: int = 0, pipeline: str = "",
              body_text_preview: str = "") -> dict:
    """추출 본문 → 기사 분할 결과 {articles[], split_confidence, reasoning, warnings}."""
    system, _ = load_prompt("Split")
    user = f"""## 입력 데이터

### 메일 메타
- 제목: {subject}
- 발신자: {from_name} <{from_address}>
- 기관명 (Triage 추정): {primary_org}
- 기사 수 추정 (Triage): {article_count_estimate}
- pipeline: {pipeline}

### 첨부 보도자료 본문 (주 정보원)

```
{press_release_text}
```

### 이메일 본문 (보조 정보원, 목차·담당자 정보)

```
{body_text_preview}
```

위 정보를 분석하여 시스템 프롬프트의 스키마대로 JSON 으로 응답하세요."""
    result = get_llm().complete_json(system, user, temperature=0.2)
    if "articles" not in result or not isinstance(result["articles"], list):
        result["articles"] = []
    return result
