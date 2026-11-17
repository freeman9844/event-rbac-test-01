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
| `assignSubscriptionReader` | `true` | 기초 `false` — 수신에 불필요한 구독 Reader 제외. 선택 실습 05A는 명시적 `true` |
| `enableIdentityEnrichment` | `false` | 기초 `false`; 선택 실습에서 ARM/Graph 권한 준비 후 `true` |
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

2026-11-17의 ACI 스타일 문서 개편은 `aci-basic-workshop-01`의 학습 목표·시간표·모듈별 시작 상태·주의·완료 체크·트러블슈팅 색인 형식을 참고했습니다. 당시 문서 작업은 기존 Function·Bicep·보조 도구를 변경하거나 Azure 자원을 생성하지 않았습니다. 이후 승인받아 구현·실행한 회사명 보강은 아래의 별도 검증입니다.

## 한정 정적 검사

기존 격리 Checkov 환경에서 컴파일한 `infra/main.json`에 ARM 검사 22 pass, 0 fail, parsing error 0을 확인했습니다. 이 결과는 다음 검사 ID를 해당 실행에서만 제외한 **한정 검사**입니다.

`CKV_AZURE_17`, `CKV_AZURE_18`, `CKV_AZURE_36`, `CKV_AZURE_43`, `CKV_AZURE_67`, `CKV_AZURE_206`, `CKV_AZURE_212`, `CKV_AZURE_213`, `CKV_AZURE_222`, `CKV_AZURE_225`

제외 범위는 클라이언트 인증서·HTTP/2·Azure Services 우회 권고, ARM 동적 이름 해석, 복제·인스턴스·가용 영역·별도 상태 확인과 Function 공개 수신 관련 PoC 제약입니다. Storage 공개 접근 차단은 제외하지 않았습니다. 전체 운영 보안·컴플라이언스 통과를 뜻하지 않으며 전역 skip 파일을 만들지 않습니다.

## 선택형 companyName 확장

[05A](05a-companyname-validation.md)는 기초 과정과 분리된 신규 사용자 실험입니다. `ENABLE_IDENTITY_ENRICHMENT=true`일 때만 관리 ID 기반 ARM·Graph 조회를 수행합니다. Graph의 `$select`에 `companyName`을 포함하고 `RbacRoleAssignmentObserved`와 동일 성공 요청을 검증합니다. 관리자용 사용자 생성·권한 부여 코드는 Function 패키지에 포함하지 않습니다.

`scripts/companyname.py`는 `<runId>-companyname.json`을 별도로 사용하며 기초 상태의 실행자·assignment를 덮어쓰지 않습니다. 기존 Activity Log 증거 헬퍼에는 선택적 대상 인자를 추가했으며 기본 호출의 자기 계정 검사는 유지합니다. 실제 API는 HTTP/CLI 경계에서 모의 처리하고 생성 응답 유실·다른 회사명·다른 요청 ID·부분 정리 실패를 테스트합니다.

2026-11-17에는 새 임시 환경을 실제 배포하여 신규 사용자 `companyName=company001`을 검증했습니다. Windows PowerShell에서 워크숍 상태·패키징·고급 실험·정리 CLI, Bicep 및 Linux 원격 빌드를 사용했습니다. 실제 Function 관리 ID의 ARM·Graph 보강 결과와 동일 성공 AppRequests를 대조했으며, 쓰기 성공 → 수신 약 5.03초, 쓰기 성공 → 보강 약 7.32초, 보강 → ingestion 약 33.38초였습니다. 상세 시각과 120초 비-RBAC 대조는 [05A 검증 상태](05a-companyname-validation.md#검증-상태)에 있습니다. Cloud Shell Bash 전체 대화형 수행과는 구분합니다.

신규 사용자·이번 Graph 권한·테스트 Reader를 먼저 제거한 뒤 Function 구독 Reader·전용 RG·소유 구독 배포 기록 두 건까지 정리했습니다. 최종 독립 조회는 08:55:08 UTC에 완료했으며, 사용자 활성 객체의 부재와 soft-delete 항목을 확인했습니다. 영구 삭제나 기존 사용자·권한 변경은 하지 않았습니다.

전체 자동 테스트는 94개가 통과했습니다. 실제 CLI에서 드러난 `ad signed-in-user show --subscription` 미지원과 토큰 조회의 `--subscription`/`--tenant` 동시 사용 금지를 회귀 테스트로 고쳤습니다. 실행자 조회는 구독 지정 Graph `/me` REST로 수행하며 반환 테넌트 검증도 유지합니다. Graph 역할 멤버십 cast는 `$count=true`와 페이지별 `ConsistencyLevel: eventual`을 사용합니다. Graph grant 전파를 제한적으로 기다리고, 생성 ID가 있으면 목록의 일시적 누락과 무관하게 삭제하며, 생성·삭제 확인이 불확실하면 정리 완료로 처리하지 않습니다.

- [보강 및 신규 사용자 승인 설계](superpowers/specs/2026-11-15-subscription-rbac-companyname-design.md#14-2026-11-17-승인된-companyname-전용-검증-확장)
- [보강 및 실제 검증 계획](superpowers/plans/2026-11-17-companyname-live-validation.md)

## 기존 워크숍 설계 자료

- [승인된 워크숍 설계](superpowers/specs/2026-11-15-rbac-event-workshop-design.md)
- [구현 계획](superpowers/plans/2026-11-15-rbac-event-workshop.md)

공개 커밋·Function ZIP에 `.workshop-state`, `.superpowers`, 가상환경·로그·로컬 설정이 들어가지 않게 합니다. 실제 환경 ID를 테스트 fixture 대신 사용하지 않습니다.
