# `.github/workflows`

[프로젝트 README](../../README.md) > [`.github`](../README.md) > `workflows`

GitHub Actions 자동화를 **결정론적 코드 검증**, **제어면 이미지 배포**, **라이브 보고서 품질
평가**로 분리합니다.

## Workflow

| 파일 | 트리거와 책임 |
|---|---|
| [`ci.yml`](ci.yml) | `main` push/PR에서 Black, isort, Flake8, import, pytest와 Bicep 산출물 drift 검사 |
| [`deploy-container-app.yml`](deploy-container-app.yml) | OIDC로 ACR 이미지를 만들고 Container App과 scheduler Job을 같은 이미지로 갱신 |
| [`report-quality.yml`](report-quality.yml) | 야간·수동·prompt 관련 PR에서 실제 Foundry/Azure 데이터를 사용한 품질 평가 |

## 로컬 등가 검사

```powershell
& .\.venv\Scripts\Activate.ps1; black --check --diff src tests scripts
& .\.venv\Scripts\Activate.ps1; isort --check-only --diff src tests scripts
& .\.venv\Scripts\Activate.ps1; flake8 src tests scripts
& .\.venv\Scripts\Activate.ps1; python -c "import src"
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\ -o "addopts=" -x
```

Bicep source와 Deploy 버튼의 JSON이 같은지도 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
```

## 운영상 중요한 점

- `deploy-container-app.yml`은 App만 갱신하지 않습니다. 같은 control-plane 이미지를 쓰는
  scheduler Job도 함께 갱신해야 예약 실행이 이전 코드를 계속 쓰지 않습니다.
- `ci.yml`의 mypy는 현재 비차단(`|| true`)이며, import와 pytest 및 Bicep drift가 차단 gate입니다.
- 라이브 품질 workflow는 저장소 variable이 없으면 안전하게 skip합니다. 권한은 OIDC identity에
  최소 범위로 부여합니다.
- 현재 `report-quality.yml`은 `scripts.evaluate_report`가 제공하지 않는 `--fail-under`와
  `--min-trajectory` 옵션을 전달합니다. 이 drift를 고치기 전에는 해당 workflow 실행을 품질
  성공 신호로 사용하지 마십시오.
- workflow 파일이나 로그에 secret 값을 출력하지 않습니다.
