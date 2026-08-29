# `src/i18n/labels`

[프로젝트 README](../../../README.md) > [`src/i18n`](../README.md) > `labels`

이메일과 관리 UI에서 사용하는 display text를 언어별 `LABELS` dict로 제공합니다. requested
language의 bundle이 일부 key만 번역해도 fallback chain으로 완전한 dict를 만듭니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`ko.py`](ko.py) | **canonical key set**과 한국어 기본 label |
| [`en.py`](en.py) | 영어 번역 bundle |
| [`ja.py`](ja.py) | 일본어 번역 bundle |
| [`__init__.py`](__init__.py) | dynamic import, raw/merged cache, fallback merge, 누락 진단 |

## 사용 예시

```python
from src.i18n.labels import get_labels, label_keys, missing_label_keys

japanese = get_labels("ja-JP")
assert set(japanese) == set(label_keys())
missing = missing_label_keys("ja")
```

새 key를 추가할 때는 먼저 `ko.py`에 넣고 다른 bundle에 번역합니다. 번역이 아직 없으면 UI는
한국어 fallback으로 계속 렌더링되지만, `missing_label_keys()`가 미완료 상태를 보여 줍니다.

## 불변식

- `ko.py` 외의 bundle에만 새 key를 추가하지 않습니다. `label_keys()`에서 보이지 않아 테스트가
  누락될 수 있습니다.
- renderer 내부에 별도 `_LABELS` 복사본을 만들지 않습니다.
- partial translation을 허용하되 릴리스 검토에서 `missing_label_keys()` 결과를 확인합니다.
- HTML을 label 값에 넣지 않고 renderer가 escaping/layout을 소유하게 합니다.
- `register_language()` 이후 raw/merged cache가 모두 비워져야 합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_i18n.py tests\test_email.py -o "addopts=" -q
```
