# `language-naturalness`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `language-naturalness`

한국어·영어·일본어 보고서의 **번역체, 문장 호응, 반복 표현, 개념 설명 깊이**를 corpus와 실제
출력으로 측정해 개선할 때 사용하는 skill입니다. 절차는 [`SKILL.md`](SKILL.md)에 있습니다.

## 코드 연결

- 언어 registry: [`src/i18n`](../../../src/i18n/)
- style guide와 translation note: [`src/agent/prompts/languages`](../../../src/agent/prompts/languages/)
- 공통 writing 원칙: [`src/agent/prompts/writing.py`](../../../src/agent/prompts/writing.py)
- 기계적 문체 검사: [`scripts/evaluate_report.py`](../../../scripts/evaluate_report.py)
- prompt 실험: [`scripts/optimize_prompt.py`](../../../scripts/optimize_prompt.py)

## 사용 예시

조립되는 한국어 guide를 코드에서 확인합니다.

```python
from src.agent.prompts.languages import get_style_guide

guide = get_style_guide("ko-KR")
```

문체 규칙과 평가기의 회귀 테스트를 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_i18n.py tests\test_quality_evaluator.py -o "addopts=" -q
```

## 불변식

- 한 문장 피드백을 곧바로 blacklist로 추가하지 않습니다. 이전 corpus의 빈도와 false positive를
  먼저 측정합니다.
- 동의어를 쫓는 금지 목록보다 모든 문장에 적용할 수 있는 구조적 rewrite 원칙을 선호합니다.
- prompt A/A 반복으로 noise floor를 구하고 그보다 작은 점수 차이는 개선으로 주장하지 않습니다.
- 새 규칙은 기존 규칙을 압축하거나 대체해 prompt dilution을 최소화합니다.
- 한국어에서 발견한 결함을 영어·일본어에 맹목적으로 복제하지 않고 각 언어의 실제 현상을
  확인합니다.
- `optimize_prompt.py`는 관찰 명령이 아니라 prompt 소스를 수정할 수 있으므로 깨끗한 diff와
  required-anchor 보존을 함께 확인합니다.
