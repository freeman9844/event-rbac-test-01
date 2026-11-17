# 개발·검증 안내

[워크숍 홈](../README.md) · [상태 복구](01-prerequisites.md) · [정리](06-cleanup-production.md)

## 로컬 개발과 Cloud Shell 구분

워크숍은 Linux Cloud Shell Bash를 대상으로 합니다. 아래는 **Windows 개발자용 PowerShell**이며 두 셸의 문법을 섞지 않습니다. Python 3.11 이상, Azure CLI·Bicep, 문서 Bash 검사에는 Git for Windows의 Bash가 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_*.py" -v
az bicep build --file .\infra\main.bicep
git --no-pager diff --check
```

Windows ARM64에서 의존성 휠이 없으면 x64 Python 3.11로 별도 가상환경을 만듭니다. 전역 도구·기존 로그인·기본 구독을 임의 변경하지 않습니다.

Linux에서 이미 의존성을 준비했다면 다음으로 테스트합니다.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
az bicep build --file infra/main.bicep
git --no-pager diff --check
```

수신기 테스트는 엄격한 이벤트 범위·작업·소스 검증과 전체 payload/claims 비기록을 검사합니다. 워크숍 테스트는 실제 상태 파일·ZIP·증거를 다루며 Azure CLI와 시계 경계만 모의 처리합니다. 문서는 상대 링크·코드 펜스·`bash -n`을 검사하고, 표시한 변경 블록은 격리된 fake CLI에서 실행하여 실제 인자·사전 검사 실패 시 중단을 검증합니다. 테스트는 Azure 리소스를 생성·삭제하지 않습니다.

## Bicep 호환성

기존 PoC 호출의 기본 동작을 유지합니다.

| 파라미터 | 기본값 | 워크숍에서 전달할 값 |
|---|---|---|
| `createResourceGroup` | `true` | `false` — 소유 태그가 있는 RG를 CLI로 먼저 생성 |
| `assignSubscriptionReader` | `true` | `false` — 수신에 불필요한 구독 Reader 제외 |
| `enableEventSubscription` | `false` | 인프라 단계 false / 코드 게시·등록 이후 true |

`enableEventSubscription=false`로 재배포해도 기존 이벤트 구독은 삭제되지 않습니다. `assignSubscriptionReader=false`도 이미 생성한 구독 Reader를 제거하지 않습니다. `capture`는 이전에 기록한 Reader가 실제로 남아 있거나 부재 조회가 실패하면 해당 ID를 새 빈 출력으로 덮어쓰지 않습니다. 구독 수준 Reader는 RG 삭제에 따라 자동 제거되지 않으므로 과거 PoC·수동 호출은 해당 실행의 정확한 ID와 소유권을 별도로 확인해야 합니다. 현재 워크숍 도구에 과거 실행의 RG·토픽·ID를 넣지 않습니다.

## 기존 PoC 실측과 워크숍 검증 범위

2026-11-15와 2026-11-17에 **기존 PoC**의 실제 임시 RG Reader, Activity Log, Event Grid, Function 및 AppRequests를 대조했습니다. 11월 17일에는 삭제된 환경을 새 전용 RG에 재배포하여 같은 수신 코드를 게시하고 실제 역할 할당으로 재검증했습니다. 환경·사용자·할당·correlation 식별자는 공개하지 않습니다.

| 관측 항목 | 2026-11-15 | 2026-11-17 |
|---|---|---|
| write 성공 → Function 수신 | 약 3.0초 | 약 6.52초 |
| AppRequests 처리 시간 | 약 36.2ms | 약 36.2ms |
| 수신 → ingestion | 약 37.1초 | 약 34.86초 |
| 해당 UTC 구간 토픽 집계 | 게시 2 / 매칭 1 / 전달 성공 1 | 게시 14 / 매칭 1 / 전달 성공 1 |
| 전달 시도 실패 | 숫자 표본 없음 | 숫자 표본 없음 |
| 임시 사용자 Reader | 삭제·부재 확인 | 삭제·부재 확인 |
| 재배포 자원 정리 | 당시 후속 개발용 인프라 유지, 이후 별도 삭제 | 신규 RG 내부 17개 자원과 사용자·관리 ID의 두 Reader 삭제·부재 확인 |

11월 17일은 write 성공 **05:26:59.2677255 UTC**, 수신 **05:27:05.7855606 UTC**, 수집 **05:27:40.6445605 UTC**였습니다. 동일 할당 ID의 수신 로그와 성공한 AppRequests를 OperationId·InvocationId로 연결했습니다. 초기 요청 로그·지표가 비어 있어도 즉시 실패나 0건으로 결론 내리지 않았으며, 수집 후 실제 표본을 확인했습니다. 전달 시도 실패의 표본 없음은 **명시적 0이 아닙니다**.

재테스트는 기존 PoC 템플릿의 기본 구독 Reader 옵션을 사용했으므로 해당 Function 관리 ID의 구독 Reader도 별도로 제거했습니다. 정상 워크숍 명령은 `assignSubscriptionReader=false`로 그 권한을 만들지 않습니다. 최신 관측의 참가자용 플로우는 [Module 05](05-rbac-event-validation.md#4-실제-관측-예시)에 있습니다.

**두 관측 모두 성능 보장이나 현재 워크숍 전체의 실제 재배포 결과가 아닙니다.** Event Grid eventId와 Activity correlationId가 같았던 관측을 일반 불변 조건으로 쓰지 않습니다. 보조 도구의 Cloud Shell 전체 절차는 모의 CLI·상태·패키징·정리 테스트와 템플릿 컴파일 등으로 검증하며, 실제 종단 간 Azure 재배포는 별도 승인·실행이 필요합니다.

2026-11-17 문서 개편은 `aci-basic-workshop-01`의 학습 목표·시간표·모듈별 시작 상태·주의·완료 체크·트러블슈팅 색인 형식을 참고합니다. 기존 Function·Bicep·보조 도구와 실습 명령은 변경하지 않으며, 이 문서 작업 때문에 Azure 자원을 새로 만들지 않습니다.

## 한정 정적 검사

기존 격리 Checkov 환경에서 컴파일한 `infra/main.json`에 ARM 검사 22 pass, 0 fail, parsing error 0을 확인했습니다. 이 결과는 다음 검사 ID를 해당 실행에서만 제외한 **한정 검사**입니다.

`CKV_AZURE_17`, `CKV_AZURE_18`, `CKV_AZURE_36`, `CKV_AZURE_43`, `CKV_AZURE_67`, `CKV_AZURE_206`, `CKV_AZURE_212`, `CKV_AZURE_213`, `CKV_AZURE_222`, `CKV_AZURE_225`

제외 범위는 클라이언트 인증서·HTTP/2·Azure Services 우회 권고, ARM 동적 이름 해석, 복제·인스턴스·가용 영역·별도 상태 확인과 Function 공개 수신 관련 PoC 제약입니다. Storage 공개 접근 차단은 제외하지 않았습니다. 전체 운영 보안·컴플라이언스 통과를 뜻하지 않으며 전역 skip 파일을 만들지 않습니다.

## 설계 자료

- [승인된 워크숍 설계](superpowers/specs/2026-11-15-rbac-event-workshop-design.md)
- [구현 계획](superpowers/plans/2026-11-15-rbac-event-workshop.md)

공개 커밋·Function ZIP에 `.workshop-state`, `.superpowers`, 가상환경·로그·로컬 설정이 들어가지 않게 합니다. 실제 환경 ID를 테스트 fixture 대신 사용하지 않습니다.
