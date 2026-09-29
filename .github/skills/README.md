# `.github/skills`

[프로젝트 README](../../README.md) > [`.github`](../README.md) > `skills`

이 디렉터리는 GitHub Copilot이 AzBrief Enterprise의 반복 작업을 수행할 때 불러오는
**저장소 고유 skill**을 주제별로 보관합니다. Foundry Agent Service의 toolbox Skill과는 다른
개발 도구이며 애플리케이션 패키지에 배포되지 않습니다.
이 문서들을 AzBrief Agent에 주입하거나 특정 구간을 읽어 운영 지침으로 컴파일하지 않습니다.

## 실행 대상 구분

| 대상 | 소유 위치 | 적용 경로 |
|---|---|---|
| GitHub Copilot | 이 디렉터리의 개발용 `SKILL.md` | 코드 편집·디버깅·테스트 절차 |
| Foundry Prompt Agents | [foundry_instructions.py](../../src/agent/foundry_instructions.py)의 운영 정책과 역할 매핑 | Provisioning으로 immutable Agent version 생성 |
| Hosted Agent의 요청별 작업 | [src/agent/prompts](../../src/agent/prompts/README.md) | 분석·보고서·구독자 재작성 프롬프트 |

KQL·보고서·언어·평가 등 실제 모델의 행동 규칙은 운영 소스에서 변경합니다. 개발용 서비스 클래스
예제·렌더러 수정 방법·corpus 점검·pytest 명령은 여기에 남깁니다. Copilot 스킬 편집 자체가 운영
Agent 지침을 바꾸지 않으며, Foundry 지침을 바꾼 경우에만 별도 Agent 버전 게시가 필요합니다.

## Skill 색인

| 디렉터리 | 사용할 때 |
|---|---|
| [`azure-service-integration/`](azure-service-integration/) | Azure SDK 기반 data-access 서비스와 Agent tool 추가 |
| [`email-template/`](email-template/) | HTML 이메일 렌더링, 라벨, ACS 전송 변경 |
| [`foundry-agent-architecture/`](foundry-agent-architecture/) | Hosted/Prompt Agent, roster, identity, 도구 경계 검토 |
| [`kql-resource-graph/`](kql-resource-graph/) | Azure Resource Graph KQL 작성과 복구 |
| [`language-naturalness/`](language-naturalness/) | ko/en/ja 문장 자연스러움과 corpus 기반 규칙 개선 |
| [`report-evaluation/`](report-evaluation/) | G-Eval LLM-as-a-Judge 평가와 개선 루프 |
| [`report-quality/`](report-quality/) | 결정론적 보고서 구조·완결성 평가 |

## 사용 방법

요청의 핵심 명사와 변경 파일에 맞는 `SKILL.md`를 먼저 읽습니다. 여러 영역이 겹치면 필요한
skill만 함께 적용합니다. 예를 들어 KQL 결과 때문에 보고서가 잘못된 경우 KQL skill로 원인을
복구한 뒤 report-evaluation skill로 실제 보고서 개선 여부를 검증합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_kql_sanitize.py tests\test_kql_retry.py -o "addopts=" -q
```

## 작성 원칙

- `SKILL.md`는 현재 코드와 검증된 운영 사실을 설명해야 하며 미래 설계를 현재 기능처럼 쓰지
  않습니다.
- 한 번의 장애에서 얻은 규칙은 재현과 검증 뒤에만 추가합니다.
- skill끼리 같은 긴 지침을 복제하지 말고 소유 skill을 링크합니다.
- 코드 변경으로 절차가 달라지면 관련 skill과 루트 문서를 같은 변경에서 갱신합니다.
