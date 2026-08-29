# `azure-service-integration`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `azure-service-integration`

새 Azure 서비스의 **data-access 계층과 Agent tool 연결**을 설계할 때 사용하는 skill입니다.
상세 절차와 예제는 [`SKILL.md`](SKILL.md)에 있습니다.

## 코드 연결

| 경로 | 책임 |
|---|---|
| [`src/services`](../../../src/services/) | Azure SDK/REST 호출, credential과 client 수명, 원시 결과 |
| [`src/agent/tools.py`](../../../src/agent/tools.py) | Pydantic 입력을 받아 서비스를 호출하는 LangChain `BaseTool` |
| [`src/agent/analyzer.py`](../../../src/agent/analyzer.py) | 도구 선택, 실행 순서, 결과 평가와 보고서 판단 |
| [`src/config.py`](../../../src/config.py) | 환경 변수와 공용 Azure credential 생성 |

서비스는 근거를 가져오고, 관련성·영향·조치 판단은 Agent 계층이 수행합니다. 동기 Azure SDK
호출은 event loop를 막지 않도록 `asyncio.to_thread()`로 감싸고 client는 첫 사용 시 만듭니다.

## 사용 예시

기존 Resource Graph 서비스의 tenant-wide 요약을 확인합니다.

```python
from src.services.resource_graph import ResourceGraphService

service = ResourceGraphService()
summary = await service.get_resource_types_summary()
```

실제 Azure identity로 smoke test를 실행할 때는 다음 명령을 사용합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local resources
```

## 불변식

- `src/services`에는 보고서 분류나 business rule을 넣지 않습니다.
- `get_azure_credential()`을 사용하고 client/credential을 닫을 수 있는 경로를 제공합니다.
- 새 dependency는 `pyproject.toml`과 `requirements.txt`에 함께 반영합니다.
- 각 기존 서비스의 성공/오류 반환 형식이 완전히 같다고 가정하지 않습니다. 구현과 직접 호출자를
  읽고 현재 계약에 맞춘 뒤, 새 계약은 테스트로 고정합니다.
- tenant-wide 서비스에서 단일 subscription 결과를 전체 tenant의 부재 증거로 취급하지 않습니다.
- 읽기 전용/동시성 안전 여부를 선언할 수 없으면 Agent는 직렬 실행해야 합니다.

## 집중 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -c "import src"
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_services.py tests\test_azure_rest.py -o "addopts=" -q
```
