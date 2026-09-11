# `foundry-agent-architecture`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `foundry-agent-architecture`

AzBrief의 **Foundry Hosted Agent, specialist Prompt Agent roster, FunctionTool, Azure MCP,
identity 경계**가 제품 아키텍처에 맞는지 감사하고 개선할 때 사용하는 skill입니다. 작업 규칙은
[`SKILL.md`](SKILL.md), 날짜가 기록된 과거 평가 근거는 [`references/`](references/README.md)에 있습니다.
과거 Agent 버전·점수·배포 결과를 현재 설치의 준비 상태로 간주하지 않습니다.

## 책임 경계

```text
Container App / Job
  -> strict v3 hosted contract (legacy v2 unbounded requests supported)
Foundry Hosted Agent
  -> LangGraph Plan-Execute-Evaluate-Report
Persisted Prompt Agents
  -> coordinator / Resource Graph / Azure MCP / Azure API / report writer / quality reviewer
```

관련 구현은 [`src/hosted_agent.py`](../../../src/hosted_agent.py),
[`src/agent/hosted_client.py`](../../../src/agent/hosted_client.py),
[`src/agent/hosted_contract.py`](../../../src/agent/hosted_contract.py),
[`src/agent/foundry_backend.py`](../../../src/agent/foundry_backend.py)에 있습니다.

## 사용 예시

새 고객 설치는 [고객 가이드](../../../infra/CUSTOMER_DEPLOYMENT.md)와
[setup_customer.ps1](../../../scripts/setup_customer.ps1)을 따릅니다. `customerSetup` 출력과
명시적인 azd 환경에서 여섯 역할과 `FOUNDRY_HOSTED_AGENT_NAME`을 연결하며, 개발용 `.env`를
사용하지 않습니다. `Verify`는 읽기 전용 준비 검사이고, 분석·archive·인증·메일 인수 후에만
`EnableSchedule -AcceptOperationalChecks`로 정기 실행을 켭니다. 기존 설치 업그레이드는
검토된 Hosted 배포 후 App/Job을 같은 digest로 갱신하는 별도 절차입니다.

원격 변경 없이 생성될 Agent 정의를 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.provision_foundry_agents --dry-run
```

이미 배포된 roster의 이름, instruction, tool, strict schema drift를 읽기 전용으로 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.provision_foundry_agents --check
```

## 불변식

- Container App과 Job은 `AzureUpdateAnalyzer`를 만들지 않으며 Hosted Agent 실패를 로컬 경로로
  우회하지 않습니다.
- Agent 정의는 immutable version으로 발행하고 runtime은 project-scoped Responses API를
  사용합니다.
- Container Apps UAMI와 Hosted Agent identity의 권한을 섞지 않습니다.
- 공식 사실은 Microsoft Learn MCP를 먼저 사용하고 Web Search는 공개 보완 근거로만 씁니다.
- tenant 상태는 Entra 인증된 read-only Azure MCP/FunctionTool로만 조회하며 Web Search에 보내지
  않습니다.
- 세 evidence specialist의 실패는 명시적 partial gap으로 격리하지만, 역할 누락·이름 중복과
  coordinator/report-writer/quality-reviewer 실패는 fail closed합니다.

## 집중 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_hosted_agent.py tests\test_hosted_client.py tests\test_hosted_contract.py tests\test_foundry_multi_agent.py -o "addopts=" -q
```
