# `report-evaluation`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `report-evaluation`

G-Eval LLM-as-a-Judge로 보고서의 **actionability, faithfulness, job relevance, structure,
architectural depth**를 평가하고 개선할 때 사용하는 skill입니다. 전체 방법론은
[`SKILL.md`](SKILL.md)에 있습니다.

## 코드 연결

| 경로 | 책임 |
|---|---|
| [`src/agent/geval.py`](../../../src/agent/geval.py) | 차원별 rubric, 병렬 judge, logprob 정규화, feedback 생성 |
| [`scripts/evaluate_report.py`](../../../scripts/evaluate_report.py) | 단건 생성·채점·반복과 artifact 저장 |
| [`scripts/evaluate_batch.py`](../../../scripts/evaluate_batch.py) | category를 층화한 fleet/holdout 측정 |
| [`eval_runs/`](../../../eval_runs/) | 로컬 생성 평가 결과; Git에서 제외됨 |

## 사용 예시

동일 seed와 표본으로 비교 가능한 baseline을 생성합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_batch --months 6 --sample 12 --seed 42 --tag baseline
```

Judge 자체의 parsing과 집계는 Azure 호출 없이 집중 검증할 수 있습니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_geval.py tests\test_critic.py -o "addopts=" -q
```

## 불변식

- 5.0은 이론적 이상이며 4.0이 production-excellent가 되도록 rubric을 유지합니다.
- 보고서가 본 근거와 judge가 본 근거의 character budget을 같게 유지합니다.
- score를 올리려고 rubric, weight, target을 완화하지 않습니다.
- 단일 stochastic sample의 총점보다 report text diff와 목표 차원의 변화를 먼저 봅니다.
- A/A noise와 holdout을 측정하지 않은 prompt 개선은 일반화됐다고 주장하지 않습니다.
- critical faithfulness flaw는 평균 점수로 상쇄하지 않습니다.
