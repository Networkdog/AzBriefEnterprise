# `scripts`

[프로젝트 README](../README.md) > `scripts`

로컬 운영·진단·평가·Foundry Agent 프로비저닝을 위한 Python CLI 모음입니다. 애플리케이션
런타임에서 import하는 business logic이 아니라, 개발자와 운영자가 명시적으로 실행하는
entry point입니다.

## 명령 색인

| Module | 용도 | 부작용 |
|---|---|---|
| [`test_local.py`](test_local.py) | 설정, RSS, resource 요약, 단건/기간 분석 | Azure/Foundry 조회; `--jsonl` 없으면 이메일 경로 사용 가능 |
| [`crawl_azure_updates.py`](crawl_azure_updates.py) | rolling RSS 밖의 update history archive 갱신 | Git 제외 `data/`에 파일 기록 |
| [`provision_foundry_agents.py`](provision_foundry_agents.py) | Prompt Agent와 enrichment roster 생성·검사·삭제 | 기본/`--delete`는 원격 변경; `--dry-run`, `--check`는 비변경 |
| [`evaluate_report.py`](evaluate_report.py) | 단건 rule-based + G-Eval 평가와 반복 rewrite | Azure/Foundry 호출, `eval_runs/` 기록 |
| [`evaluate_batch.py`](evaluate_batch.py) | category 층화 fleet/holdout 평가 | Azure/Foundry 호출, `eval_runs/` 기록 |
| [`run_quality_loop.py`](run_quality_loop.py) | mock 결과의 빠른 deterministic 품질 loop | 로컬 artifact 가능 |
| [`optimize_prompt.py`](optimize_prompt.py) | 고정 sample로 한국어 prompt A/B 최적화 | prompt source를 수정할 수 있음 |

## 자주 쓰는 예시

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local config
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local list -n 20
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local resources
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local analyze --latest --jsonl results_local.jsonl
```

보고서 하나를 HTML까지 생성하고 최대 3회 개선합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_report --latest --with-html --iterate 3 --target 4.5
```

동일 seed의 fleet sample은 변경 전후 비교에 사용합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_batch --months 6 --sample 12 --seed 42 --tag baseline
```

## 운영 원칙

- 모든 명령은 `.venv` 활성화 뒤 실행합니다.
- `test_local analyze --jsonl`은 이메일을 건너뛰고 record를 append합니다. 재실행하면 같은 파일에
  누적된다는 점을 고려합니다.
- live RSS는 최근 약 200건의 rolling window입니다. 과거 기간은 crawler가 만든 local history와
  병합됩니다.
- `provision_foundry_agents --delete`는 파괴적이며 명시적 의도 없이 실행하지 않습니다.
- `optimize_prompt`의 metric이 측정하지 않는 규칙을 지우지 않도록 required anchors와 diff를
  확인합니다.
- 자격증명이나 live tenant 근거가 없으면 deterministic test와 과거 artifact만으로 검증한 범위를
  명확히 구분합니다.
