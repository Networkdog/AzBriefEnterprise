# `foundry-agent-architecture/references`

[프로젝트 README](../../../../README.md) > [Foundry architecture skill](../README.md) > `references`

Foundry 아키텍처 판단의 **시점별 평가 결과와 검증 증거**를 보관합니다. 이 디렉터리는 런타임
구성의 원본이 아니라, 특정 시점의 코드와 배포 상태를 어떤 근거로 평가했는지 추적하는 감사
자료입니다.

## 문서

| 파일 | 내용 |
|---|---|
| [`assessment.md`](assessment.md) | 책임 분리, roster, 도구, 보안, 운영 준비 상태에 대한 아키텍처 평가 |

## 사용 예시

Hosted Agent 구조를 바꾸기 전에 평가의 미해결 항목과 live evidence를 읽고, 현재 코드와 아직
같은지 확인합니다. roster 검사는 다음 명령으로 재현할 수 있습니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.provision_foundry_agents --check
```

## 기록 원칙

- 평가 날짜, 대상 commit/version, 검증 명령과 관찰 결과를 함께 남깁니다.
- 과거의 pass 결과를 현재 배포의 보증으로 재사용하지 않습니다.
- 비밀값, access token, 전체 tenant payload를 증거 문서에 넣지 않습니다.
- 현재 설계 규칙은 부모 [`SKILL.md`](../SKILL.md)와 루트 README에 반영하고, 이곳에는 장문의
  근거와 시점별 차이만 둡니다.
