# `tests`

[프로젝트 README](../README.md) > `tests`

제어면, Hosted contract, Agent loop, KQL, 이메일, i18n, 서비스와 Enterprise 구성의 회귀를
pytest로 검증합니다. 대부분 외부 Azure/Foundry 호출을 mock해 빠르고 결정론적으로 실행합니다.

## 영역별 찾기

| 테스트 묶음 | 대표 파일 |
|---|---|
| Hosted 경계 | [`test_hosted_contract.py`](test_hosted_contract.py), [`test_hosted_client.py`](test_hosted_client.py), [`test_hosted_agent.py`](test_hosted_agent.py) |
| Agent loop와 resilience | [`test_analyzer.py`](test_analyzer.py), [`test_context_store.py`](test_context_store.py), [`test_resilience.py`](test_resilience.py) |
| Foundry specialist team | [`test_foundry_backend.py`](test_foundry_backend.py), [`test_foundry_multi_agent.py`](test_foundry_multi_agent.py), [`test_provision_foundry_agents.py`](test_provision_foundry_agents.py) |
| KQL과 Azure evidence | [`test_kql_sanitize.py`](test_kql_sanitize.py), [`test_kql_retry.py`](test_kql_retry.py), [`test_impact_tools.py`](test_impact_tools.py), [`test_billing.py`](test_billing.py) |
| 제어면 | [`test_api.py`](test_api.py), [`test_admin.py`](test_admin.py), [`test_archive.py`](test_archive.py), [`test_mcp_server.py`](test_mcp_server.py), [`test_orchestrator.py`](test_orchestrator.py), [`test_scheduler.py`](test_scheduler.py) |
| 전달과 언어 | [`test_email.py`](test_email.py), [`test_i18n.py`](test_i18n.py), [`test_quality_evaluator.py`](test_quality_evaluator.py) |
| Data access | [`test_services.py`](test_services.py), [`test_checkpoint.py`](test_checkpoint.py), [`test_archive_store.py`](test_archive_store.py), [`test_rss_parser.py`](test_rss_parser.py) |
| 결정론적 평가 | [`test_archive_evaluation.py`](test_archive_evaluation.py), [`test_quality_evaluator.py`](test_quality_evaluator.py), [`test_quality_campaign.py`](test_quality_campaign.py) |
| Security/config | [`test_security.py`](test_security.py), [`test_config.py`](test_config.py), [`test_enterprise_config.py`](test_enterprise_config.py) |

[`conftest.py`](conftest.py)는 `sample_rss_xml`, `sample_update`, `sample_analysis_result`처럼 여러
test가 공유하는 realistic fixture를 제공합니다.

## 실행 예시

가장 빠른 변경 범위 test를 먼저 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_hosted_contract.py tests\test_hosted_client.py -o "addopts=" -q
```

전체 suite는 project default coverage option을 명시적으로 제거하고 첫 실패에서 멈출 수 있습니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\ -o "addopts=" -x
```

CI와 같은 coverage gate가 필요하면 project addopts를 유지하거나 명시적으로 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\ --cov=src --cov-report=term-missing --cov-fail-under=40
```

## 테스트 작성 원칙

- 외부 API unit test는 network 대신 mock response로 성공, transient failure, malformed payload,
  authorization failure를 분리합니다.
- `get_settings()`가 cache되므로 환경 변수를 바꾸는 test는 cache와 관련 singleton을 정리합니다.
- Foundry role/roster test는 machine의 실제 `.env` 값을 상속하지 않도록 관련 변수를 명시적으로
  제거하거나 설정합니다.
- async test는 `pytest-asyncio`의 auto mode를 사용합니다.
- collection error는 “한 테스트 실패”가 아니라 해당 파일 전체가 미검증된 상태입니다. 즉시
  해결하고 숨겨진 실패를 확인합니다.
- local suite 통과는 live identity, quota, private network, deployed Agent version을 검증하지
  않습니다. 운영 smoke test 결과와 구분해 보고합니다.
- Quality campaign test는 기간 dataset hash/split, 다층 release gate, A/A paired 비교, 안전 회귀
  우선순위, case checkpoint/resume, deferred transient retry, dimension-error blocker, fake Hosted
  snapshot의 report artifact 생성을 네트워크 없이 검증합니다.