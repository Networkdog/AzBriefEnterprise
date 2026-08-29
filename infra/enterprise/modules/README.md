# `infra/enterprise/modules`

[프로젝트 README](../../../README.md) > [`infra/enterprise`](../README.md) > `modules`

상위 template에서 독립 배포 경계가 필요한 작은 Bicep module을 둡니다. 현재는 internal Container
Apps environment의 runtime-generated domain을 Private DNS zone 이름으로 사용하는 module 하나가
있습니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`internal-ingress-dns.bicep`](internal-ingress-dns.bicep) | environment default domain의 zone, VNet link, wildcard A record 생성 |

Container Apps environment의 `defaultDomain`과 `staticIp`는 environment가 생성된 뒤에만 알 수
있습니다. ARM은 deployment 시작 시 resource name을 계산해야 하므로, runtime 값을 zone 이름으로
받는 작업을 별도 module deployment 경계로 분리합니다.

## 사용 예시

module을 단독 배포하지 않고 부모 template을 compile해 계약을 검증합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\enterprise\main.bicep --stdout
```

부모 [`main.bicep`](../main.bicep)은 `internalIngressOnly`와 VNet mode가 모두 참일 때만 module을
호출하고, environment의 default domain, static IP, VNet resource ID를 넘깁니다.

## 불변식

- wildcard record는 Container Apps environment의 static IP를 가리켜야 합니다.
- zone link의 `registrationEnabled`는 `false`입니다.
- public ingress 또는 perimeter/public network profile에서 이 private zone을 만들지 않습니다.
- runtime-generated name 문제를 피하려고 값을 복제하거나 이름을 추측하지 않습니다.
