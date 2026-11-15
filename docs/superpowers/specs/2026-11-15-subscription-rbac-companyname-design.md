# 구독 RBAC 역할 할당과 사용자 companyName 확인 설계

- 작성일: 2026-11-15
- 상태: 2026-11-15 사용자 승인 완료. 제품 코드 작성과 Azure 배포는 아직 수행하지 않았다.
- 작업 규모: 새 Function과 이벤트 연결을 만드는 소규모 검증 프로젝트.

## 1. 목적과 확인된 요구

Azure Subscription에서 RBAC 역할 할당이 발생하면 할당받은 대상, 역할, 적용 범위를 확인한다. 대상이 사용자이면 Microsoft Entra ID 사용자 속성인 `companyName`도 함께 조회한다. 초기 결과 확인 장소는 Application Insights 로그이다.

사용자가 선택한 내용은 다음과 같다.

- 이벤트 감지에는 Event Grid를 사용한다.
- 이벤트 처리에는 Azure Function을 사용한다.
- 사용자 추가 속성은 `companyName`이다.
- 결과는 Application Insights 로그에서 확인한다.

현재 저장소에는 초기 커밋만 있고 애플리케이션이나 인프라 파일은 없다. 기존 구현과의 호환성 제약은 없다.

## 2. 목표와 제외 범위

### 목표

- 한 구독과 그 하위 리소스 그룹 및 리소스 범위에서 성공한 역할 할당 쓰기를 감지한다.
- 역할 할당 리소스에서 `principalId`, `principalType`, `roleDefinitionId`, `scope`를 조회한다.
- 역할 정의에서 사람이 읽을 수 있는 역할명을 조회한다.
- 직접 할당 대상이 사용자이면 사용자 식별 정보와 `companyName`을 조회한다.
- 성공, 속성 미설정, 적용 불가, 조회 실패를 구별해 구조화 로그로 남긴다.
- 실제 이벤트 발생부터 Application Insights 조회까지 검증할 수 있는 절차를 제공한다.

### 제외 범위

- 역할 할당 삭제, 실패한 할당 시도, 과거 이력 수집.
- Microsoft Entra 디렉터리 역할, PIM 적격성 및 활성화 전체 수명주기 감시.
- 관리 그룹에서 발생한 할당을 하위 구독의 이벤트로 대체 감지하는 기능.
- 그룹 구성원 확장 및 그룹 구성원 변경에 따른 유효 권한 계산.
- 서비스 주체와 관리 ID의 소속 회사 추론.
- 알림 발송, 자동 역할 회수, 별도 UI, 별도 업무 결과 저장소.
- 정확히 한 번 처리, 손실 없는 감사 원장, 자동 재처리 큐.

### 피해야 할 결과

- 이벤트를 발생시킨 사용자와 역할을 받은 사용자를 혼동하지 않는다.
- `companyName` 미설정과 Graph 조회 실패를 모두 빈 문자열로 처리하지 않는다.
- 조회 시점의 `companyName`을 할당 시점의 확정적인 과거 값으로 표현하지 않는다.
- 성공한 역할 할당 쓰기를 무조건 새로운 권한 부여라고 단정하지 않는다. 동일 할당에 대한 재요청도 있을 수 있다.
- 테스트용 역할 할당을 임의의 운영 사용자나 운영 리소스에 만들지 않는다.

## 3. 검토한 접근 방식

| 접근 방식 | 장점 | 고려 사항 | 결정 |
|---|---|---|---|
| Event Grid와 Azure Function | ARM 및 Graph 조회, 오류 분류, 테스트를 코드로 명확히 관리 | 코드 배포와 유지보수 필요 | 사용자가 선택 |
| Event Grid와 Logic Apps | 단계별 조회를 시각적으로 관리 | 오류 분기와 재시도가 늘면 흐름이 복잡해짐 | 이번에는 사용하지 않음 |

Azure Monitor Activity Log 기반 알림은 향후 대안이 될 수 있으나, 사용자가 선택한 Event Grid 경로를 승인 없이 대체하지 않는다.

## 4. 구현을 위해 제안하는 기본 결정

이 절은 사용자 지정 사항이 아니라 설계 작성자의 제안이다. 설계 승인 시 함께 채택하되 변경 가능하다.

- 언어는 Python, Azure Functions Python v2 프로그래밍 모델을 사용한다.
- 네이티브 Event Grid 트리거를 사용한다. 임의의 익명 HTTP 수신 API를 추가하지 않는다.
- 실행 환경은 Linux 기반 Flex Consumption을 기본으로 한다. 배포 지역에서 지원 여부를 먼저 확인한다.
- 인프라는 Bicep, 배포 및 권한 설정 안내는 Azure CLI와 PowerShell을 기준으로 한다. azd는 도입하지 않는다.
- Function의 시스템 할당 관리 ID로 ARM과 Microsoft Graph를 호출한다. 클라이언트 비밀을 만들지 않는다.
- Python 표준 로거에 JSON 메시지를 기록하고 Azure Functions의 기본 Application Insights 연동으로 수집한다. 이번 검증에는 별도 계측 SDK를 추가하지 않는다.
- 기본 대상은 하나의 Azure 공용 클라우드 구독과 그 구독의 Entra 테넌트이다.
- 사용자, 그룹, 서비스 주체에 대한 직접 할당은 모두 기록하되 사용자에 대해서만 사용자 속성을 보강한다.

## 5. 논리 구성

```text
Azure Subscription
  └─ Event Grid 시스템 토픽: Microsoft.Resources.Subscriptions
       └─ 이벤트 구독: 역할 할당 쓰기 성공 필터
            └─ Azure Function: Event Grid 트리거
                 ├─ ARM: 역할 할당 조회
                 ├─ ARM: 역할 정의 조회
                 ├─ Graph: 사용자이면 companyName 조회
                 └─ JSON 로그 → Application Insights
```

Function 실행용 Storage Account, Application Insights 및 연결된 Log Analytics Workspace가 필요하다. 별도 업무 데이터베이스나 메시지 큐는 초기 범위에 포함하지 않는다. 신규 리소스는 전용 리소스 그룹에 배치하되 이벤트 소스와 읽기 권한 범위는 대상 구독이다.

이벤트 구독은 Function 배포와 권한 준비가 끝난 뒤 연결한다. 리소스 그룹 및 실제 이름, Azure 지역, 구독 ID와 테넌트 ID는 배포 시 입력한다. 실제 배포 전 대상 구독과 비용 발생을 사용자에게 확인한다.

## 6. 이벤트 선택과 확인이 필요한 가정

이벤트 소스는 `/subscriptions/{subscriptionId}`이다. 이벤트 전달 스키마는 Event Grid 스키마를 사용한다.

| 설정 | 값 |
|---|---|
| 시스템 토픽 유형 | `Microsoft.Resources.Subscriptions` |
| 이벤트 유형 | `Microsoft.Resources.ResourceWriteSuccess` |
| 고급 필터 키 | `data.operationName` |
| 연산자 | `StringIn` |
| 비교 값 | `Microsoft.Authorization/roleAssignments/write` |

특정 구독 수준 할당의 subject 접두사로 한정하지 않는다. 그렇게 하면 리소스 그룹과 개별 리소스 범위의 할당을 누락할 수 있다. Function에서도 이벤트 유형, 작업명, 소스 구독, 역할 할당 리소스 경로를 검증해 의도하지 않은 ARM 조회를 방지한다. 비교는 ARM 식별자의 대소문자 차이를 고려한다.

공식 문서는 구독의 ARM 쓰기 이벤트와 작업명 필터링을 설명하지만, 이번 작업에서는 실제 구독의 `roleAssignments/write` 이벤트를 아직 관측하지 않았다. 따라서 다음을 검증의 첫 관문으로 둔다.

1. 사용자가 지정한 테스트 범위에 임시 Reader 역할을 할당한다.
2. 해당 작업이 Activity Log에 성공으로 기록됐는지 확인한다.
3. 실제 Event Grid 이벤트와 `subject`, `data.resourceUri`, `data.operationName`을 확인한다.
4. 역할 할당 리소스 ID로 후속 조회가 가능한지 확인한다.

실제 이벤트가 수신되지 않으면 배포, 필터, 전달 지표를 점검하고 관측 사실을 보고한다. 이를 검증 완료라고 표현하거나 합성 이벤트 테스트로 대체하지 않는다. 소스 지원에 문제가 있으면 Activity Log 기반 대체 구성은 별도 설계 승인을 받는다.

## 7. 처리 흐름

1. 수신 이벤트에서 `id`, `eventTime`, `subject`, `data.operationName`, `data.resourceUri`, `data.correlationId`를 읽고 필요한 필드를 검증한다.
2. 역할 할당 ID는 `subject`를 기본으로 사용한다. `subject`가 없을 때만 검증된 `data.resourceUri`를 사용하고, 둘 다 있으면 같은 리소스인지 확인한다. 다른 구독, 잘못된 리소스 유형, 잘못된 형식은 오류로 기록한다.
3. 고정된 ARM 호스트에 역할 할당 ID를 사용해 API 버전 `2022-04-01`로 조회한다.
4. 응답의 `principalId`, `principalType`, `roleDefinitionId`, `scope`를 확보한다.
5. 해당 역할 정의를 조회해 `roleName`을 확보한다.
6. `principalType`이 `User`이면 같은 테넌트의 Graph 사용자 API에서 `id`, `displayName`, `userPrincipalName`, `companyName`을 명시적으로 선택해 조회한다.
7. 사용자가 아닌 대상은 사용자 API를 호출하지 않는다. 대상 ID, 유형, 역할 및 범위를 기록하고 `companyName`은 적용 불가로 표시한다.
8. 결과를 JSON 한 건으로 기록한다. 조회한 시각을 별도로 남긴다.

`claims`와 `authorization.evidence`는 할당받은 대상의 식별 근거로 사용하지 않는다. 이번 목적에 불필요한 작업 수행자의 전체 클레임은 수집하지 않는다.

## 8. 실행 및 배포 권한

### Function 실행 관리 ID

- 대상 구독의 Azure RBAC Reader 역할로 역할 할당과 역할 정의를 읽는다. PoC 단순성을 위해 Reader를 선택하며, 쓰기 권한은 부여하지 않는다.
- 더 엄격한 환경에서는 `Microsoft.Authorization/roleAssignments/read`와 `Microsoft.Authorization/roleDefinitions/read`를 허용하는 사용자 지정 역할로 축소할 수 있다. 초기 구현의 필수 구성은 아니다.
- Microsoft Graph의 애플리케이션 앱 역할 `User.Read.All`을 관리 ID의 서비스 주체에 부여한다. 관리자 권한이 필요한 별도 작업이며 Azure Reader 역할로 대체되지 않는다.
- `User.ReadBasic.All`만으로 `companyName`까지 읽을 수 있다고 가정하지 않는다.
- Function 실행용 Storage와 텔레메트리 설정은 선택한 호스팅 환경의 요구 권한을 따른다. 저장소 접근 자격 증명이나 Function 키는 Git에 기록하지 않는다.

### 배포 및 초기 설정 수행자

- Azure 리소스 생성, 구독 소스의 Event Grid 이벤트 구독 생성, 필요한 Azure 역할 부여 권한이 필요하다.
- Graph 앱 역할 부여는 해당 작업이 가능한 관리자가 별도로 실행한다.
- 리소스 배포 스크립트가 테넌트 수준 Graph 권한을 몰래 부여하지 않는다. 관리자 단계를 명시적으로 분리한다.
- Graph 권한 부여가 불가능하면 전체 보강 조회 검증이 막힌 상태로 보고한다. 조회 실패를 사용자 속성 미설정으로 바꾸지 않는다.

## 9. 로그 계약과 확인 방법

성공적인 관측 결과의 `eventName`은 `RbacRoleAssignmentObserved`로 고정한다.

| 필드 | 의미 |
|---|---|
| `schemaVersion` | 초기 값 `1` |
| `eventName` | 결과 유형 |
| `eventId`, `correlationId` | 원본 이벤트 추적 식별자 |
| `eventTime`, `observedAt` | 이벤트 발생 시각과 조회 시각, UTC |
| `subscriptionId`, `roleAssignmentId` | 원본 구독과 역할 할당 |
| `principalId`, `principalType` | 직접 할당 대상 |
| `displayName`, `userPrincipalName` | 사용자 식별 정보, 비사용자는 null |
| `roleDefinitionId`, `roleName`, `scope` | 역할과 적용 범위 |
| `companyName` | 조회한 사용자 속성의 원래 값, 비사용자는 null |
| `enrichmentStatus` | `succeeded`, `companyNameNotSet`, `notApplicable` |

사용자 조회 결과가 null 또는 빈 문자열인 경우 원래 값을 보존하고 `companyNameNotSet`으로 구별한다. `companyName`을 이메일 도메인 등으로 추정해서 채우지 않는다. 게스트 사용자는 리소스 테넌트의 게스트 사용자 객체에 기록된 값을 읽으며, 홈 테넌트의 회사 정보를 대신 조회하지 않는다.

실패는 `RbacRoleAssignmentEnrichmentFailed`로 기록한다. `eventId`, 확인된 역할 할당 ID, 실패 단계, 오류 분류, HTTP 상태와 서비스 요청 ID를 포함하되 토큰, 원본 응답 본문, 전체 이벤트를 기록하지 않는다. 성공 결과와 실패 결과를 섞지 않는다.

Application Insights의 `traces`에서 JSON `message`를 해석해 대상, 역할, 적용 범위, `companyName`을 조회하는 KQL을 제공한다. Log Analytics Workspace에서 직접 조회할 때의 `AppTraces`와 `Message` 차이도 안내한다.

해당 Function은 검증 전용이므로 초기에는 Application Insights 샘플링을 비활성화하고 사용자 로그의 Information 수준 수집을 보장한다. 그래도 일일 수집 한도, 수집 지연, 서비스 장애 등으로 로그가 누락될 수 있으므로 감사 원장으로 보장하지 않는다. 조회 권한과 보존 기간은 실습 환경의 정책을 따른다.

## 10. 오류, 재시도와 중복

- ARM과 Graph 호출에 명시적인 타임아웃을 둔다. 일시적 네트워크 오류, 429, 재시도 가능한 5xx만 유한하게 재시도하고 `Retry-After`를 고려한다.
- 역할 할당 조회의 404는 전파 지연 가능성을 고려해 짧게 재시도한다. 계속 없으면 이미 삭제됐거나 조회 불가한 대상으로 오류 기록한다. 할당 대상을 추정하지 않는다.
- 사용자 조회의 404는 대상 사용자가 삭제됐거나 테넌트에서 조회되지 않는 오류로 분류한다.
- 401, 403, 잘못된 입력과 반복 재시도 실패는 명시적으로 오류 로그를 남기고 호출 실패로 표면화한다. 성공 형태의 빈 결과로 반환하지 않는다.
- Event Grid는 최소 한 번 전달하며 순서를 보장하지 않는다. 초기에는 중복 실행을 허용하고 KQL에서 `eventId` 기준으로 결과를 정리할 수 있게 한다.
- Event Grid의 전달 재시도와 Function 내부 실행 및 외부 API 재시도는 별개이다. Function이 이벤트를 접수한 뒤 발생한 오류가 항상 Event Grid 재전달로 복구된다고 약속하지 않는다.
- 초기 PoC에는 별도 dead-letter 저장소와 업무 재처리 큐를 추가하지 않는다. 전달 재시도가 끝나거나 재시도하지 않는 전달 오류가 발생하면 이벤트가 유실될 수 있음을 안내하고 Event Grid 전달 지표를 확인하는 절차를 제공한다.
- 손실 방지가 필수인 운영 용도로 확대하려면 큐, dead-letter, 재처리와 영속적 중복 제거를 별도로 설계한다.

## 11. 검증과 완료 기준

### 로컬 및 정적 검증

- 실제 Azure 호출 없이 이벤트 해석 및 ARM/Graph 응답을 모킹한 집중 단위 테스트를 작성한다.
- 정상 사용자와 `companyName`, 속성 미설정, 비사용자, 잘못된 소스 및 작업, 조회 권한 오류, 일시 오류와 재시도 소진을 검증한다.
- `claims`의 작업 수행자가 할당받은 대상과 다른 사례를 포함한다.
- Bicep 빌드와 배포 전 what-if 또는 검증을 수행한다.

### 실제 Azure 통합 검증

- 사용자가 승인한 구독, 테스트 사용자, 테스트 범위, 지역을 사용한다. 테스트 사용자의 `companyName`은 읽기만 하고 임의로 수정하지 않는다.
- 처음에는 리소스 그룹 범위에 최소 권한의 임시 Reader 할당을 만들고 Event Grid 원본 이벤트 수신부터 확인한다.
- 가능하면 구독 및 개별 리소스 범위도 승인된 테스트 대상으로 검증한다. 검증하지 않은 범위를 검증 완료라고 표시하지 않는다.
- 원본 역할 할당 정보, Graph의 `companyName`, Application Insights 기록이 일치해야 한다.
- 무관한 리소스 쓰기가 결과 로그를 만들지 않는지 확인한다.
- 테스트용 역할 할당 ID를 기록하고 해당 임시 할당만 제거한다. 다른 할당은 삭제하지 않는다.
- 실제 계정 권한이나 Azure 접근이 없으면 로컬 검증과 실제 통합 검증을 구분하고, 배포 완료나 종단 간 검증 완료라고 보고하지 않는다.

### 완료 산출물

- Python Function 및 집중 단위 테스트.
- Bicep 인프라와 명시적인 관리자 권한 설정 절차.
- 한국어 배포, KQL 조회, 실제 이벤트 검증 및 정리 안내.
- 실제로 수행한 검사와 미수행 검사, 남은 운영 제약에 대한 결과.

## 12. 구현자에게 맡기는 사항

파일 분리, SDK 또는 HTTP 클라이언트 선택, 지원 중인 Python 런타임 세부 버전, 리소스 명명, API 재시도 횟수와 제한 시간의 보수적인 기본값은 구현자가 정한다. 새 런타임이나 라이브러리는 공식 지원 여부를 확인한다.

구독 ID, 테넌트 ID, 지역, 테스트 사용자와 테스트 대상 범위는 배포 직전에 받는 실행 매개변수이다. 이 값이 없다는 이유로 임의의 계정이나 구독에 배포하지 않는다.

## 13. 공식 참고 자료

- [Azure Subscription 이벤트 스키마](https://learn.microsoft.com/en-us/azure/event-grid/event-schema-subscriptions)
- [Event Grid 필터링](https://learn.microsoft.com/en-us/azure/event-grid/event-filtering)
- [역할 할당 ID로 조회](https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/get-by-id?view=rest-authorization-2022-04-01)
- [Microsoft Graph 사용자 조회 및 권한](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0)
- [Microsoft Graph 사용자 속성](https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0)
- [Azure Functions Event Grid 트리거](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-event-grid-trigger)
- [Azure Functions 실행 모니터링](https://learn.microsoft.com/en-us/azure/azure-functions/functions-monitoring)
- [Event Grid 전달과 재시도](https://learn.microsoft.com/en-us/azure/event-grid/delivery-and-retry)
