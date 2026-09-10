# `scripts`

[프로젝트 README](../README.md) > `scripts`

로컬 운영·진단·평가·Foundry Agent 프로비저닝을 위한 Python CLI 모음입니다. 애플리케이션
런타임에서 import하는 business logic이 아니라, 개발자와 운영자가 명시적으로 실행하는
entry point입니다.

## 명령 색인

| Module | 용도 | 부작용 |
|---|---|---|
| [`test_local.py`](test_local.py) | 설정, RSS, resource 요약, 단건/기간 분석 | Azure/Foundry 조회; `--jsonl` 없으면 이메일 경로 사용 가능 |
| [preview_email.py](preview_email.py) | SYNTHETIC ko/en/ja 단건·digest 디자인 미리보기 | 로컬 HTML만 저장; Azure 호출·이메일 발송·전송 client 초기화 없음 |
| [preview_web.py](preview_web.py) | SYNTHETIC Admin/Archive/Feedback 브라우저 미리보기 | 루프백 서버, 메모리 내 관리 변경, 모의 접수증; 실분석·이메일 발송·영구 저장 없음 |
| [`crawl_azure_updates.py`](crawl_azure_updates.py) | rolling RSS 밖의 update history archive 갱신 | Git 제외 `data/`에 파일 기록 |
| [`provision_foundry_agents.py`](provision_foundry_agents.py) | 여섯 specialist Prompt Agent 생성·검사·삭제 | 기본/`--delete`는 원격 변경; `--dry-run`, `--check`는 비변경 |
| [`evaluate_report.py`](evaluate_report.py) | 단건 rule-based + G-Eval 평가와 반복 rewrite | Azure/Foundry 호출, `eval_runs/` 기록 |
| [`evaluate_batch.py`](evaluate_batch.py) | category 층화 fleet/holdout 평가 | Azure/Foundry 호출, `eval_runs/` 기록 |
| [`quality_campaign.py`](quality_campaign.py) | 기간 snapshot, diagnosis/holdout, A/A, Hosted 진단, paired release gate | Azure/Foundry 호출, `eval_runs/` 기록 |
| [`foundry_evaluation.py`](foundry_evaluation.py) | 완료된 campaign report의 Foundry cloud evaluator 교차 검증 | Foundry eval definition/run 생성, source run 아래 결과 기록 |
| [`evaluate_archive.py`](evaluate_archive.py) | 10k 불변 버전의 cursor/filter/integrity/PII/latency 평가 | 임시 File backend, `eval_runs/archive_*` 기록 |
| [`run_quality_loop.py`](run_quality_loop.py) | mock 결과의 빠른 deterministic 품질 loop | 로컬 artifact 가능 |
| [`optimize_prompt.py`](optimize_prompt.py) | 고정 sample로 한국어 prompt A/B 최적화 | prompt source를 수정할 수 있음 |

## 자주 쓰는 예시

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local config
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local list -n 20
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local resources
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local analyze --latest --jsonl results_local.jsonl
```

이메일 디자인은 실제 공지·고객 데이터가 아닌 **SYNTHETIC 합성 데이터**로 오프라인 점검합니다.
전송 설정과 종료 이력을 mock하고, 각 언어의 단건·digest마다 전체 스타일과 `<style>` 블록을
제거한 inline-only 버전을 생성합니다. `--language all`은 ko/en/ja HTML 총 12개를 저장하며
Azure 호출이나 이메일 발송은 하지 않습니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.preview_email --output-dir out/email-editorial-preview --language all
```

`--language`는 `ko`, `en`, `ja`도 받습니다. 출력 디렉터리를 생략하면 임시 디렉터리를 만듭니다.
합성 미리보기는 전송 성공, 전체 suite 또는 브라우저·이메일 client 검증의 근거가 아닙니다.

웹 화면은 같은 합성 예제를 재사용하는 루프백 전용 서버에서 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.preview_web --port 8765
```

`http://127.0.0.1:8765/admin`, `/archive`, `/feedback`를 엽니다. 포트가 사용 중이면 `--port`를
바꾸십시오. 관리 변경은 메모리 내에서만 반영되며 서버 재시작 때 초기화합니다. Archive는 실제 v1
스키마로 검증한 30개 합성 기록을 제공하고, run 요청은 409로 차단합니다. 피드백 접수증은 모의
응답으로 저장하지 않습니다. 미리보기 서버를 외부에 배포하거나 운영 인증 검증의 근거로 쓰지 않습니다.

보고서 하나를 HTML까지 생성하고 최대 3회 개선합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_report --latest --with-html --iterate 3 --target 4.5
```

동일 seed의 fleet sample은 변경 전후 비교에 사용합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_batch --months 6 --sample 12 --seed 42 --tag baseline
```

장기 개선에서는 먼저 기간 dataset을 고정합니다. `local`은 현재 source의 Hosted harness와 실제
Prompt Agent roster를 쓰는 inner loop이고, `hosted`는 배포 후보를 검증하는 final loop입니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.quality_campaign prepare --from 2026-06-01 --to 2026-08-29 --sample 24 --seed 42 --output eval_runs/campaign-q3
python -m scripts.quality_campaign run --campaign eval_runs/campaign-q3 --tag baseline-a --runtime local --split diagnosis --concurrency 1 --use-azd-env
python -m scripts.quality_campaign run --campaign eval_runs/campaign-q3 --tag baseline-a --runtime local --split diagnosis --concurrency 1 --use-azd-env --resume-run eval_runs/campaign-q3/runs/<interrupted-run>
python -m scripts.quality_campaign compare --baseline <baseline-a> --candidate <baseline-b> --mode aa --output eval_runs/aa-noise.json
python -m scripts.quality_campaign compare --baseline <baseline-run> --candidate <candidate-run> --noise-floor 0.15 --output eval_runs/comparison.json
python -m scripts.foundry_evaluation --run-dir <campaign>/runs/<completed-run> --use-azd-env
```

`summary.json`은 source/worktree, dataset, Agent roster, Hosted contract lineage와 semantic,
rule-based, trajectory, action-safety gate를 함께 보존합니다. `trace_ids.jsonl`의 ID로 로컬 log와
Application Insights의 Hosted/Prompt Agent trace를 연결합니다. 비공개 chain-of-thought는 기록하지 않습니다.
장시간 run은 각 시도를 `attempts/*.json`에 남기고 최종 case 결과를 `records/*.json`에 원자적으로
기록합니다. Transient connection/rate-limit 오류와 generation placeholder는 전체 첫 pass 뒤 한 번
재시도하며 `case_retry_count`와 `recovered_case_count`를 집계합니다. `run.json`과
`progress.json`이 있는 중단 run은 `--resume-run`으로 재개할 수 있지만, dataset·case 순서·runtime·
source/worktree·immutable Agent version이 모두 같아야 합니다. 완료 중 source나 Agent version이
바뀐 run은 `run_valid=false`이며 비교와 출시 판정에 사용할 수 없습니다. Concurrency도
`run.json`에 고정되는 A/A 실험 축입니다. 각 분석이 내부에서 specialist 세 개를 병렬 호출하므로
기본값 1을 사용하고, 측정된 capacity 근거 없이 분석 여러 건을 겹치지 않습니다.
Worktree fingerprint는 tracked binary diff뿐 아니라 Git이 ignore하지 않은 untracked 파일의 경로와
byte도 포함합니다. Campaign manifest의 schema/rubric/threshold/Hosted contract가 현재 runner와
다르면 기존 결과를 섞지 않고 `prepare`부터 다시 실행합니다.
최종 case 오류, critical flaw, blocked/unverified action뿐 아니라 G-Eval dimension 오류도
non-compensating blocker이며 candidate 비교에서 증가하면 평균 점수와 관계없이 regression입니다.

`foundry_evaluation`은 성공한 canonical report와 공개 update context만 inline dataset으로 보내고
email-like 값, raw tenant evidence, subscriber data, private reasoning은 보내지 않습니다. 기본 evaluator는
`coherence`, `fluency`, `relevance`, `task_adherence`입니다. `indirect_attack`은 지원 리전에서만
`--evaluators`로 opt-in합니다. 기본 실행은 `run_valid=true`인 완료 run만 받으며, smoke 진단에서만
`--allow-unvalidated-run`을 사용합니다. Foundry 결과는 독립 보조 신호이고 local faithfulness,
trajectory, action-safety blocker를 상쇄하지 않습니다. `--eval-id`는 item schema, evaluator 순서,
data mapping, judge deployment가 현재 요청과 정확히 같은 definition만 재사용하며, 하나라도 다르면
원격 run을 만들기 전에 fail closed합니다.

아카이브의 pagination과 검색 완전성은 외부 호출 없이 재현합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_archive --records 10000
```

## 운영 원칙

- 모든 명령은 `.venv` 활성화 뒤 실행합니다.
- 일회성 로그 파서, 검증 scratch script와 테스트 산출물은 repository root에 두지 않습니다.
  재사용 가능한 도구만 이 디렉터리에 추가하고, 임시 코드는 `scripts/.tmp-*` 아래에서 사용합니다.
- `test_local analyze --jsonl`은 이메일을 건너뛰고 record를 append합니다. 재실행하면 같은 파일에
  누적된다는 점을 고려합니다.
- live RSS는 최근 약 200건의 rolling window입니다. 과거 기간은 crawler가 만든 local history와
  병합됩니다.
- `provision_foundry_agents --delete`는 파괴적이며 명시적 의도 없이 실행하지 않습니다.
- `optimize_prompt`의 metric이 측정하지 않는 규칙을 지우지 않도록 required anchors와 diff를
  확인합니다.
- 자격증명이나 live tenant 근거가 없으면 deterministic test와 과거 artifact만으로 검증한 범위를
  명확히 구분합니다.
