"""설정·상수 (이식명세 §3,§7 기준)."""
from __future__ import annotations

# atpaju 카테고리(섹션) 코드 — 이식명세 §7
CATEGORY_CODES = {
    "S1N1": "정치행정",
    "S1N2": "의회의원",
    "S1N3": "사회경제",
    "S1N4": "파주소식",
    "S1N5": "교육",
    "S1N6": "문화예술생활과학",
    "S1N7": "오피니언",
    "S1N8": "형편대로광고",
    "S1N10": "미분류",
}
DEFAULT_CATEGORY = "S1N10"  # 미분류(안전 기본값)

# 같은 보도자료가 여러 포맷일 때 본문 추출 우선순위 — 이식명세 §3
ATTACH_PRIORITY = {"pdf": 100, "hwpx": 90, "docx": 80, "hwp": 75, "doc": 70, "txt": 50}

# 본문 후보에서 제외(이미지/압축)
NON_ARTICLE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "zip"}


def normalize_category(code_or_name: str | None) -> str:
    """AI가 준 카테고리 코드/이름을 유효한 코드로 정규화. 모르면 미분류."""
    if not code_or_name:
        return DEFAULT_CATEGORY
    s = code_or_name.strip()
    if s in CATEGORY_CODES:
        return s
    for code, name in CATEGORY_CODES.items():        # 이름으로 들어온 경우
        if name == s:
            return code
    return DEFAULT_CATEGORY
