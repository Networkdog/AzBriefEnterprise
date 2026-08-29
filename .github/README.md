# `.github`

[프로젝트 README](../README.md) > `.github`

이 디렉터리는 AzBrief Enterprise의 **개발 규칙, 자동화, 재사용 작업 절차**를 보관합니다.
애플리케이션 런타임에는 포함되지 않으며, 기여자와 GitHub Copilot 및 GitHub Actions가
저장소를 같은 방식으로 다루도록 만드는 제어 문서입니다.

## 구성

| 경로 | 목적 |
|---|---|
| [`copilot-instructions.md`](copilot-instructions.md) | 아키텍처, 코딩 규칙, 검증 절차의 기준 문서 |
| [`prompts/`](prompts/README.md) | VS Code Chat에서 실행하는 장시간 작업 프롬프트 |
| [`skills/`](skills/README.md) | 작업 유형별 저장소 고유 지식과 절차 |
| [`workflows/`](workflows/README.md) | CI, 이미지 배포, 라이브 품질 평가 자동화 |

## 사용 예시

코드를 바꾸기 전에는 먼저 적용되는 지침과 관련 skill을 읽습니다. 예를 들어 이메일 렌더러를
수정한다면 다음 순서로 범위를 확인합니다.

1. [`copilot-instructions.md`](copilot-instructions.md)의 공통 규칙을 읽습니다.
2. [`skills/email-template/SKILL.md`](skills/email-template/SKILL.md)의 이메일 전용 규칙을 읽습니다.
3. 변경 후 해당 디렉터리의 집중 테스트와 전체 import 검사를 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -c "import src"
```

## 불변식

- 이곳의 문서는 런타임 환경 변수나 비밀값을 저장하는 장소가 아닙니다.
- 코드 동작을 바꾸면 루트 README, 이 지침 문서, 관련 `SKILL.md`가 실제 코드와 함께
  갱신되어야 합니다.
- workflow가 통과한다고 라이브 Foundry/Azure 경로까지 검증된 것은 아닙니다. 각 workflow가
  사용하는 identity와 데이터 범위를 별도로 확인합니다.
- 저장소 지침과 skill이 충돌하면 더 구체적인 작업 지침을 따르되, 보안과 fail-closed 원칙은
  완화하지 않습니다.
