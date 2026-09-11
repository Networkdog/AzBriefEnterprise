# `.github/workflows`

[프로젝트 README](../../README.md) > [`.github`](../README.md) > `workflows`

GitHub Actions 자동화를 **결정론적 코드 검증**, **제어면 이미지 배포**, **라이브 보고서 품질
평가**로 분리합니다.

## Workflow

| 파일 | 트리거와 책임 |
|---|---|
| [`ci.yml`](ci.yml) | 변경 경로가 일치하는 `main` push/PR에서 Linux 코드/coverage, 두 Bicep 컴파일·ARM drift, Windows 고객 배포 검사 수행을 정의 |
| [`deploy-container-app.yml`](deploy-container-app.yml) | OIDC로 ACR 이미지를 만들고 Container App과 scheduler Job을 같은 이미지로 갱신 |
| [`report-quality.yml`](report-quality.yml) | 야간·수동·prompt 관련 PR에서 실제 Foundry/Azure 데이터를 사용한 품질 평가 |

## 로컬 등가 검사

```powershell
& .\.venv\Scripts\Activate.ps1; black --check --diff src tests scripts
& .\.venv\Scripts\Activate.ps1; isort --check-only --diff src tests scripts
& .\.venv\Scripts\Activate.ps1; flake8 src tests scripts
& .\.venv\Scripts\Activate.ps1; python -c "import src"
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\ -o "addopts=" -x --cov=src --cov-report=term-missing --cov-fail-under=40
```

Bicep source와 Deploy 버튼의 JSON이 같은지도 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1
$compiled = Join-Path $env:TEMP 'azbrief-compiled.json'
az bicep build --file infra\enterprise\main.bicep --outfile $compiled
if ((Get-FileHash $compiled).Hash -ne (Get-FileHash infra\azbrief-enterprise-deploy.json).Hash) {
  throw 'Compiled ARM template is stale.'
}
```

`bicep` job은 컴파일러를 **0.46.1**로 고정하고 임시 Enterprise JSON과 추적 중인 ARM을
바이트 단위로 비교합니다. Azure MCP 템플릿도 별도로 컴파일합니다. 컴파일러를 바꿀 때는
검토된 ARM 재생성과 버전 고정을 함께 갱신해야 합니다.

`customer-deployment` job은 Windows, Python 3.11, PowerShell 7과 프로젝트 `.venv`에서 실행합니다.
가짜 CLI를 사용하는 검사이므로 고객 Azure 리소스·모델·이메일을 호출하지 않습니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m pytest tests/test_customer_deployment.py tests/test_deploy_dev_script.py tests/test_enterprise_config.py -o addopts= -x -q
```

트리거 경로는 `.github/workflows/ci.yml`, `src/**`, `tests/**`, `scripts/**`,
`requirements.txt`, `pyproject.toml`, `azure.yaml`, `infra/**`입니다. 루트 README나 `.github`
문서만 바뀌면 이 경로 필터로 CI가 자동 실행되지는 않으므로 필요한 문서·링크 검사는
로컬에서도 수행합니다.

## 운영상 중요한 점

- `ci.yml`의 `permissions`는 workflow 최상위에서 `contents: read`만 허용합니다. 기존 고객
  배포 계약 테스트가 권한 선언 위치와 CI 자체 변경의 push/PR 트리거를 확인합니다.
  YAML 파싱이나 로컬 pytest 통과를 GitHub Actions 스키마 검증·원격 실행 성공으로 대체하지 않습니다.
- `deploy-container-app.yml`은 App만 갱신하지 않습니다. 같은 control-plane 이미지를 쓰는
  scheduler Job도 함께 갱신해야 예약 실행이 이전 코드를 계속 쓰지 않습니다.
- 이 이미지 전용 workflow는 초기 hello-world의 포트·probe 전환을 수행하지 않습니다. 새 고객
  설치는 [고객 가이드](../../infra/CUSTOMER_DEPLOYMENT.md)와 `setup_customer.ps1`을 따르고,
  기존 환경의 검증·되돌림이 필요한 업그레이드는 `deploy_dev.ps1`을 사용합니다.
- `ci.yml`의 mypy는 현재 비차단(`|| true`)이며, import와 pytest 및 Bicep drift가 차단 gate입니다.
- 라이브 품질 workflow는 저장소 variable이 없으면 안전하게 skip합니다. 권한은 OIDC identity에
  최소 범위로 부여합니다.
- 현재 `report-quality.yml`은 `scripts.evaluate_report`가 제공하지 않는 `--fail-under`와
  `--min-trajectory` 옵션을 전달합니다. 이 drift를 고치기 전에는 해당 workflow 실행을 품질
  성공 신호로 사용하지 마십시오.
- workflow 파일이나 로그에 secret 값을 출력하지 않습니다.
