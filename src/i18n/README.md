# `src/i18n`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `i18n`

AzBrief가 지원하는 보고서 언어와 fallback chain의 **단일 registry**입니다. 이메일 label,
prompt style guide, action verification message, 설정 validator가 같은 언어 code를 공유하게 합니다.

## 구성

| 경로 | 책임 |
|---|---|
| [`__init__.py`](__init__.py) | `LanguageSpec`, 등록, 정규화, fallback, bundle resolution, cache hook |
| [`labels/`](labels/README.md) | 언어별 UI/이메일 label과 누락 진단 |
| [`../agent/prompts/languages`](../agent/prompts/languages/) | report writing guide와 translation notes |

기본 언어는 `ko`이며 `en`, `ja`가 등록되어 있습니다. `ko-KR`, `ko_KR`, `KO`는 모두 `ko`로
정규화됩니다. 미등록 언어도 `get_language()`가 synthetic spec을 반환하므로 report 생성이
crash하지 않고 label은 기본 언어로 fallback합니다.

## 사용 예시

```python
from src.i18n import fallback_chain, get_language, normalize_language

assert normalize_language("KO_kr") == "ko"
assert get_language("fr").english_name == "French"
assert fallback_chain("fr") == ("fr", "ko")
```

plugin/test에서 언어를 runtime 등록할 수도 있습니다.

```python
from src.i18n import LanguageSpec, register_language

register_language(
    LanguageSpec(code="fr", english_name="French", native_name="Français")
)
```

## 새 언어 절차

1. `__init__.py`에서 `LanguageSpec`을 등록합니다.
2. 필요하면 `labels/<code>.py`에 partial/full label bundle을 추가합니다.
3. 필요하면 `src/agent/prompts/languages/<code>.py`에 curated guide를 추가합니다.
4. regional tag, fallback, cache invalidation, email rendering을 테스트합니다.

## 불변식

- 언어 code 목록을 config, email, prompt에 다시 hard-code하지 않습니다.
- fallback chain은 cycle 없이 항상 `DEFAULT_LANGUAGE`에서 끝납니다.
- `get_language()`와 `get_labels()`는 미등록/partial 언어 때문에 `None` 또는 `KeyError`를 만들지
  않습니다.
- runtime 등록 뒤 label/style-guide cache clearer가 실행되어야 합니다.
- code/identifier/CLI/KQL은 번역하지 않고 user-facing prose와 label만 지역화합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_i18n.py -o "addopts=" -q
```
