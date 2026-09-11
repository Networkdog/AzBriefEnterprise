# `infra`

[프로젝트 README](../README.md) > `infra`

AzBrief Enterprise의 Azure 인프라를 두 배포 단위로 나눠 보관합니다. 제품 전체 topology와
읽기 전용 Azure MCP Server는 identity와 lifecycle이 다르므로 같은 template에 합치지 않습니다.

## 구성

| 경로 | 목적 |
|---|---|
| [`enterprise/`](enterprise/README.md) | Foundry, Container App/Job, network, state, email, monitoring의 원본 Bicep |
| [`azbrief-enterprise-deploy.json`](azbrief-enterprise-deploy.json) | Deploy to Azure 버튼이 사용하는 compiled ARM template |
| [createUiDefinition.json](createUiDefinition.json) | 같은 버튼의 모델·레지스트리·메일·Entra 입력 폼 |
| [CUSTOMER_DEPLOYMENT.md](CUSTOMER_DEPLOYMENT.md) | 고객 사전 조건, 단계별 설정, 인수·정기 실행·복구 절차 |
| [`azbrief-enterprise.parameters.example.json`](azbrief-enterprise.parameters.example.json) | 비밀값 없는 deployment parameter 예시 |
| [`azure-mcp-server/`](azure-mcp-server/README.md) | Azure MCP specialist가 호출하는 별도 Entra 인증 read-only Container App |

## 사용 예시

제품 template을 Bicep source에서 다시 생성합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
```

Azure MCP template은 별도로 정적 compile할 수 있습니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\azure-mcp-server\infra\main.bicep --stdout
```

## Source of truth

- 고객 버튼은 ARM과 UI 파일을 같은 소스 버전으로 게시해야 합니다. 기본 준비용 앱은 80 포트와
  `/`로 응답하고 Job은 수동입니다. 실제 AzBrief의 8000 포트·`/health` 전환과 동일 digest 적용은
  [setup_customer.ps1](../scripts/setup_customer.ps1)의 `Application` 단계가 수행합니다.
- `customerSetup` 출력에는 대상·이름만 넣고 비밀 값이나 구독자 정보를 추가하지 않습니다.
  동시 분석 기본값은 1이며 인수 후 `EnableSchedule -AcceptOperationalChecks`로 정기 실행을 켭니다.
- `enterprise/main.bicep`이 제품 topology의 원본입니다. checkpoint와 immutable analysis archive
  container도 여기서 함께 정의하며 compiled JSON을 손으로 수정하지 않습니다.
- Azure MCP는 `azure-mcp-server/infra/main.bicep`과 그 module이 원본입니다.
- `enterprise/main.json`은 현재 CI나 Deploy 버튼이 참조하지 않는 별도 snapshot입니다. 배포
  산출물로 사용하지 말고, 필요성이 확인될 때 원본/생성 경로를 정리해야 합니다.
- parameter example에는 실제 client secret, API key, 구독자 개인정보를 넣지 않습니다.

## 검증

CI는 Enterprise Bicep을 임시 JSON으로 compile한 뒤 추적 중인
`azbrief-enterprise-deploy.json`과 byte-level drift를 검사합니다. 정적 compile은 schema와 타입
오류를 잡지만 subscription policy, quota, RBAC 전파, private DNS 연결까지 증명하지는 않습니다.
실제 배포 전에는 대상 환경에서 what-if/validate를 별도로 수행해야 합니다.
CI의 Bicep 버전은 산출물 생성에 사용한 0.46.1로 고정하며 Azure MCP 템플릿도 컴파일합니다.
고객 설정은 별도 Windows CI에서 가짜 CLI로 검증합니다. Portal 폼은 공식 CreateUIDefinition
스키마와 함께 검사하고 고객 배포 전에 Portal sandbox에서도 확인합니다.
