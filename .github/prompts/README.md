# `.github/prompts`

[프로젝트 README](../../README.md) > [`.github`](../README.md) > `prompts`

VS Code Chat에서 반복 가능하게 실행할 **저장된 작업 프롬프트**를 둡니다. 런타임의 LLM
system prompt는 [`src/agent/prompts`](../../src/agent/prompts/)에 있으며, 이 디렉터리의
파일은 개발 에이전트가 저장소 자체를 분석하고 수정할 때만 사용됩니다.

## 파일

| 파일 | 용도 |
|---|---|
| [`self-improve-reports.prompt.md`](self-improve-reports.prompt.md) | 실제 보고서를 생성·평가하고 반복 결함의 원인을 소스에 반영하는 장시간 개선 루프 |

## 사용 예시

VS Code Chat의 프롬프트 선택기에서 **AzBrief 자율 보고서 개선 루프**를 선택하고 다음처럼
인수를 입력합니다.

```text
budget=8 target=4.5 period=2026-06
```

특정 업데이트만 진단할 때는 URL을 전달할 수 있습니다.

```text
budget=3 target=4.5 url=https://azure.microsoft.com/updates?id=<update-id>
```

이 프롬프트는 `scripts/quality_campaign.py`로 기간 payload와 diagnosis/holdout을 고정하고,
변경 전 A/A noise와 holdout baseline을 만든 뒤 한 번에 하나의 source-level 가설을 검증합니다.
평가 산출물은 Git에서 제외된 `eval_runs/`에 남고 source/worktree, dataset, Agent roster,
`trace_id` lineage를 보존합니다. 자격증명이 없으면 degraded 평가 경로를 선택합니다.

## 주의사항

- 이 작업은 관찰용 prompt가 아니라 소스 수정까지 수행할 수 있는 개발 절차입니다.
- 한 업데이트의 점수만 올리는 하드코딩, 평가 기준 완화, verbosity 증가를 개선으로 인정하지
  않습니다.
- 라이브 평가에는 Azure/Foundry 호출 비용과 권한이 필요합니다.
- Prompt Agent provisioning 또는 Hosted Agent 배포는 사용자 승인 뒤 수행하며, 배포 후보는 같은
  campaign을 `--runtime hosted`로 다시 통과해야 합니다.
- `.env`, 토큰, 구독자 개인정보를 프롬프트 파일이나 평가 산출물에 기록하지 않습니다.
