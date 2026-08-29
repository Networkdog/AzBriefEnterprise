# `src/agent/prompts/languages`

[프로젝트 README](../../../../README.md) > [`src/agent/prompts`](../README.md) > `languages`

보고서 문장과 구독자 customization에 사용할 **언어별 writing guide**를 제공합니다. 지원 언어
목록의 source of truth는 이 디렉터리가 아니라 [`src/i18n`](../../../i18n/) registry입니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`__init__.py`](__init__.py) | module lazy load, cache, 미등록 언어 generic guide 생성 |
| [`ko.py`](ko.py) | 한국어 주술 호응, 번역체 회피, 문장 다양성, concept box 규칙 |
| [`en.py`](en.py) | 영어 technical prose와 category/subject 일치 규칙 |
| [`ja.py`](ja.py) | 일본어 formal register와 자연스러운 technical writing 규칙 |

언어 module은 report phase용 긴 `STYLE_GUIDE`와 subscriber customization용 짧은
`TRANSLATION_NOTES`를 선택적으로 제공합니다. 과거 상수명인 `KOREAN_STYLE_GUIDE` 등도 loader가
호환 처리합니다.

## 사용 예시

```python
from src.agent.prompts.languages import (
    get_style_guide,
    get_translation_notes,
    has_curated_guide,
)

guide = get_style_guide("ko-KR")
notes = get_translation_notes("ja")
assert has_curated_guide("en")
```

미등록 `fr` 같은 code도 registry가 만든 language metadata를 사용해 비어 있지 않은 generic guide를
반환합니다. UI label은 별도의 i18n fallback chain을 사용합니다.

## 새 언어 추가

1. 먼저 `src/i18n/__init__.py`에 `LanguageSpec`을 등록합니다.
2. 필요할 때 `<code>.py`에 `STYLE_GUIDE`와 `TRANSLATION_NOTES`를 추가합니다.
3. 이메일 label은 `src/i18n/labels/<code>.py`에 별도로 추가합니다.
4. regional tag 정규화와 cache invalidation을 `tests/test_i18n.py`로 검증합니다.

## 불변식

- style guide 파일을 지원 언어 registry로 사용하거나 언어 code를 다른 모듈에 hard-code하지
  않습니다.
- 번역 대상에는 Azure service/resource/SKU/CLI/KQL 식별자를 원문 그대로 유지합니다.
- concept box는 첫 언급 근처에서만 사용하고 설명할 개념이 없으면 억지로 채우지 않습니다.
- 한 언어의 자연스러움 규칙을 다른 언어에 그대로 번역해 넣지 않습니다.
- cache는 runtime `register_language()` 뒤 무효화되어 새 module/spec을 볼 수 있어야 합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_i18n.py tests\test_quality_evaluator.py -o "addopts=" -q
```
