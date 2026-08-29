# `foundry-agent-architecture`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `foundry-agent-architecture`

AzBrief의 **Foundry Hosted Agent, Prompt Agent, enrichment roster, FunctionTool, Azure MCP,
identity 경계**가 제품 아키텍처에 맞는지 감사하고 개선할 때 사용하는 skill입니다. 작업 규칙은
[`SKILL.md`](SKILL.md), 현재 평가 근거는 [`references/`](references/README.md)에 있습니다.

## 책임 경계

```text
Container App / Job
  -> strict hosted contract
Foundry Hosted Agent
  -> LangGraph Plan-Execute-Evaluate-Report
Persisted Prompt Agents
  -> role-specific model/instructions/tools
```

관련 구현은 [`src/hosted_agent.py`](../../../src/hosted_agent.py),
[`src/agent/hosted_client.py`](../../../src/agent/hosted_client.py),
[`src/agent/hosted_contract.py`](../../../src/agent/hosted_contract.py),
[`src/agent/foundry_backend.py`](../../../src/agent/foundry_backend.py)에 있습니다.

## 사용 예시

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
- enrichment stage 실패는 격리할 수 있지만 필수 runtime role 실패는 fail closed입니다.

## 집중 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_hosted_agent.py tests\test_hosted_client.py tests\test_hosted_contract.py tests\test_foundry_multi_agent.py -o "addopts=" -q
```
