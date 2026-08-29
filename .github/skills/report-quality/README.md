# `report-quality`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `report-quality`

보고서와 이메일의 **구조적 완결성, 필수 필드, action 구체성, 스캔성**을 결정론적 규칙으로
검사할 때 사용하는 skill입니다. 평가 항목과 개선 절차는 [`SKILL.md`](SKILL.md)에 있습니다.

## 코드 연결

- [`scripts/evaluate_report.py`](../../../scripts/evaluate_report.py)의 `ReportQualityEvaluator`
  가 빠른 100점 기계 평가를 수행합니다.
- [`scripts/run_quality_loop.py`](../../../scripts/run_quality_loop.py)는 mock 결과로 반복 가능한
  품질 loop를 제공합니다.
- [`src/email/templates.py`](../../../src/email/templates.py)는 실제 독자가 보는 HTML 구조를
  결정합니다.
- [`src/agent/prompts/report`](../../../src/agent/prompts/report/)는 report schema와 category frame을
  지시합니다.

## 사용 예시

Azure 자격증명 없이 mock 품질 회귀를 빠르게 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.run_quality_loop
```

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_quality_evaluator.py tests\test_email.py -o "addopts=" -q
```

## 불변식

- 점수 항목을 추가하거나 이동해도 총점 계약은 100을 유지합니다.
- 단순히 필드가 채워졌다는 이유로 무의미한 “영향 없음” 문장을 보상하지 않습니다.
- Capability category는 활용 기회, Change category는 영향과 위험이라는 frame을 유지합니다.
- 기계 평가는 의미적 faithfulness와 자연스러움을 대신하지 않으므로 G-Eval 및 실제 렌더링
  검토와 함께 사용합니다.
- edge case의 정직한 빈 값과 근거 부족 표명을 결함으로 오인하지 않습니다.