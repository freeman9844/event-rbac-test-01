# 구독 RBAC와 companyName 확인 Implementation Plan

> **For agentic workers:** 필수 하위 스킬은 `superpowers:subagent-driven-development` 또는 `superpowers:executing-plans`이다. 승인된 실행 방식에 맞춰 작업별로 구현하고 체크박스로 진행 상태를 기록한다.

**Goal:** 구독의 역할 할당 쓰기 성공 이벤트를 받아 직접 할당 대상, 역할, 적용 범위와 사용자 `companyName`을 Application Insights에서 확인한다.

**Architecture:** 구독 소스 Event Grid 시스템 토픽이 작업명으로 이벤트를 걸러 Python Event Grid 트리거 Function에 전달한다. Function의 시스템 할당 관리 ID로 ARM 역할 할당과 역할 정의, Microsoft Graph 사용자 정보를 조회하고 JSON 로그를 남긴다. 실제 RBAC 이벤트 수신을 먼저 입증한 다음 보강 조회를 연결한다.

**Tech Stack:** Python 3.11, Azure Functions Python v2, `azure-functions`, `azure-identity`, `azure-core`, `requests`, 표준 `unittest`, Bicep, Azure CLI, PowerShell, Application Insights.

**Spec:** `docs\superpowers\specs\2026-11-15-subscription-rbac-companyname-design.md`

## Global Constraints

- 이벤트 감지에는 Event Grid를 사용한다.
- 이벤트 처리에는 Azure Function을 사용한다.
- 사용자 추가 속성은 `companyName`이다.
- 결과는 Application Insights 로그에서 확인한다.
- Function의 시스템 할당 관리 ID로 ARM과 Microsoft Graph를 호출한다. 클라이언트 비밀을 만들지 않는다.
- 네이티브 Event Grid 트리거를 사용한다. 임의의 익명 HTTP 수신 API를 추가하지 않는다.
- 사용자, 그룹, 서비스 주체에 대한 직접 할당은 모두 기록하되 사용자에 대해서만 사용자 속성을 보강한다.
- `claims`와 `authorization.evidence`는 할당받은 대상의 식별 근거로 사용하지 않는다.
- 조회 시점의 `companyName`을 할당 시점의 확정적인 과거 값으로 표현하지 않는다.
- 성공한 역할 할당 쓰기를 무조건 새로운 권한 부여라고 단정하지 않는다. 동일 할당에 대한 재요청도 있을 수 있다.
- 실제 배포 전 대상 구독과 비용 발생을 사용자에게 확인한다.
- 실제 계정 권한이나 Azure 접근이 없으면 로컬 검증과 실제 통합 검증을 구분하고, 배포 완료나 종단 간 검증 완료라고 보고하지 않는다.
- 초기 PoC에는 별도 dead-letter 저장소와 업무 재처리 큐를 추가하지 않는다.
- 문서와 실행 안내는 한국어, 로컬 경로 및 PowerShell 예제는 Windows 경로를 사용한다.
- 의존성은 매니페스트를 만든 뒤 설치하며, 실제 검증 없이 버전 호환성을 단정하지 않는다. 기존 린터는 없으므로 별도 린터나 테스트 프레임워크를 추가하지 않는다.

## Review Focus

- 구독·리소스 그룹·개별 리소스 범위 및 ARM 경로의 대소문자 차이: 모두 같은 소스 구독의 유효한 할당으로 처리한다. 작업 1에서 검증한다.
- 절대 URL, 다른 구독, 상충하는 subject/resourceUri: 외부 조회 전에 거부하고 토큰을 다른 호스트로 보내지 않는다. 작업 1과 3에서 검증한다.
- null·빈 문자열·필드 누락·사용자 삭제: 속성 미설정과 응답 오류를 구별한다. 작업 3과 4에서 검증한다.
- 429 Retry-After가 허용 대기 시간을 넘거나 403이 반복되는 경우: 무한 대기나 성공 형태의 빈 결과를 만들지 않는다. 작업 3에서 검증한다.
- 중복·역순 이벤트와 부분 실패: 중복 수신을 허용하되 동일 eventId로 추적하고 성공과 실패 로그를 분리한다. 작업 4에서 검증한다.

---

## 파일 구조와 책임

| 파일 | 책임 |
|---|---|
| `function_app.py` | Event Grid 바인딩, SDK 이벤트 변환, 관리 ID 클라이언트 준비, 안전한 로그와 실패 전파 |
| `rbac_monitor\events.py` | 이벤트 검증과 `RoleAssignmentEvent` |
| `rbac_monitor\azure_client.py` | 읽기 전용 ARM/Graph 호출, 응답 검증, 유한 재시도와 오류 분류 |
| `rbac_monitor\processor.py` | 조회 결과를 승인된 관측 로그 계약으로 조합 |
| `rbac_monitor\__init__.py` | 패키지 표시 |
| `requirements.txt` | 네 가지 런타임 의존성의 검증된 버전 범위 |
| `host.json` | Functions 확장 번들, Information 로그, 초기 샘플링 비활성화 |
| `.gitignore`, `.funcignore` | 로컬 설정·자격 증명·가상환경 제외, 테스트·문서의 배포 제외 |
| `infra\main.bicep` | 구독 범위 진입점, 전용 리소스 그룹, Reader 역할, 단계별 모듈 연결 |
| `infra\workload.bicep` | Storage, Flex Function, 관리 ID, Application Insights와 Workspace |
| `infra\eventing.bicep` | 구독 소스 시스템 토픽과 정확한 필터의 Function 대상 이벤트 구독 |
| `scripts\Grant-GraphUserRead.ps1` | 관리자가 명시적으로 실행하는 관리 ID의 Graph 앱 역할 부여 |
| `tests\__init__.py`, `tests\fixtures.py` | 테스트 패키지와 합성 이벤트·HTTP 응답 생성기 |
| `tests\test_events.py` | 입력 이벤트와 리소스 ID 검증 |
| `tests\test_azure_client.py` | 실제 통신 없는 API 응답·권한·재시도 테스트 |
| `tests\test_processor.py`, `tests\test_function_app.py` | 보강 조회 결과와 실제 진입 함수의 로그 계약 |
| `docs\queries.kql` | Application Insights 및 Workspace 조회 예제 |
| `README.md` | 한국어 배포, 관리자 설정, 검증, 비용·운영 한계와 정리 절차 |

`tests\fixtures.py`에는 `SUBSCRIPTION_ID`, `ASSIGNMENT_ID`, `PRINCIPAL_ID`, `ROLE_DEFINITION_ID`와 `event_envelope(role_assignment_id: str = ASSIGNMENT_ID) -> dict[str, object]`를 정의한다. 실제 사용자 정보는 사용하지 않는다. HTTP 테스트가 필요해지는 작업 3에서 `http_response(status: int, body: object, headers: dict[str, str] | None = None) -> requests.Response`를 추가한다.

## 작업 순서와 중단 조건

작업 1 → 작업 2 → 작업 3 → 작업 4 순서이다. 작업 2의 실제 이벤트 수신을 입증하지 못하면 원인을 보고하고 다음 기능 구현을 중단한다. 합성 이벤트의 성공은 실제 소스 지원 확인을 대신하지 않는다.

실제 배포를 승인받지 못한 경우 작업 2의 정적 검증까지만 수행하고 통합 검증을 차단 상태로 남긴다. 구독이나 테스트 사용자를 임의로 선택하지 않는다.

### Task 1: 테스트 가능한 이벤트 수신 Function

**Files:**
- Create: `function_app.py`, `rbac_monitor\__init__.py`, `rbac_monitor\events.py`
- Create: `requirements.txt`, `host.json`, `.gitignore`, `.funcignore`
- Test: `tests\__init__.py`, `tests\fixtures.py`, `tests\test_events.py`, `tests\test_function_app.py`

**Interfaces:**
- 입력: Event Grid 스키마의 envelope와 환경 변수 `EXPECTED_SUBSCRIPTION_ID`.
- 출력: `RoleAssignmentEvent` 동결 dataclass. 필드는 `event_id: str`, `event_time: str`, `correlation_id: str | None`, `subscription_id: str`, `role_assignment_id: str`.
- 출력: `parse_event(envelope: Mapping[str, object], expected_subscription_id: str) -> RoleAssignmentEvent`; 잘못된 입력에는 `EventValidationError(ValueError)`.
- 출력: `handle_role_assignment(event: azure.functions.EventGridEvent) -> None`; Azure 함수 이름은 `RbacAssignmentObserver`.

- [ ] **Step 1: 실패할 이벤트 테스트를 작성한다.**

`test_accepts_assignment_at_each_scope_case_insensitively`는 구독, 리소스 그룹, 개별 리소스 아래의 할당 ID와 대소문자를 바꾼 ID를 각각 통과시킨다. `test_falls_back_to_resource_uri_only_without_subject`는 subject가 없을 때만 fallback을 허용한다. `test_rejects_mismatched_ids_foreign_subscription_and_url`은 각 입력에서 `EventValidationError`가 발생함을 확인한다.

```python
def test_accepts_subscription_assignment(self):
    event = parse_event(event_envelope(), SUBSCRIPTION_ID)
    self.assertEqual(event.role_assignment_id, ASSIGNMENT_ID)
    self.assertEqual(event.subscription_id, SUBSCRIPTION_ID)

def test_rejects_non_rbac_operation(self):
    envelope = event_envelope()
    data = envelope["data"]
    assert isinstance(data, dict)
    data["operationName"] = "Microsoft.Storage/storageAccounts/write"
    with self.assertRaises(EventValidationError):
        parse_event(envelope, SUBSCRIPTION_ID)
```

추가로 잘못된 이벤트 유형, data의 잘못된 형식, 필수 eventId·시각 누락을 거부한다. 진입 함수 테스트는 잘못된 입력에서 ERROR 로그가 있고 정상 관측 로그는 없음을 확인한다.

- [ ] **Step 2: 테스트가 구현 부재로 실패하는지 확인한다.**

실행: `python -m unittest discover -s .\tests -p "test_events.py" -v`

기대: `rbac_monitor.events` 또는 `parse_event`가 없어 실패한다. 입력 fixture 자체의 구문 오류는 먼저 고친다.

- [ ] **Step 3: 이벤트 검증과 최소 수신 함수를 구현한다.**

`parse_event`는 `Microsoft.Resources.ResourceWriteSuccess`, `Microsoft.Authorization/roleAssignments/write`, 소스 topic과 대상 구독을 검사한다. 역할 할당 경로는 해당 구독 아래이며 마지막 리소스 유형이 `Microsoft.Authorization/roleAssignments/{guid}`여야 한다. URL, query, fragment와 경로 이탈 표현은 거부한다. 두 리소스 ID가 있으면 대소문자를 무시해 일치 여부를 확인한다.

진입 함수는 SDK 객체의 `id`, `event_type`, `event_time`, `subject`, `topic`, `get_json()`으로 envelope를 만든다. 정상 수신은 임시 진단 이벤트 `RbacRoleAssignmentReceived`에 eventId와 역할 할당 ID만 JSON으로 기록한다. 실패는 전체 envelope 없이 ERROR로 기록하고 다시 발생시킨다.

네 가지 의존성은 `azure-functions`, `azure-identity`, `azure-core`, `requests`이다. 지원되는 릴리스 범위를 확인해 `requirements.txt`에 기록한 뒤 `python -m pip install -r .\requirements.txt`를 실행한다. 이벤트 발행 SDK, Graph 전체 SDK, 별도 계측 SDK는 추가하지 않는다.

- [ ] **Step 4: 수신 함수와 이벤트 테스트를 함께 통과시킨다.**

실행: `python -m unittest tests.test_events tests.test_function_app -v`

기대: 모두 PASS. 정상 수신 로그에 claims·토큰·전체 이벤트가 없고, 잘못된 입력에는 실패 로그가 있다.

- [ ] **Step 5: 최소 수신 기능을 커밋한다.**

대상 파일만 stage한 뒤 커밋 메시지 `feat: add validated RBAC event receiver`를 사용한다. 세션에 적용되는 필수 Git 트레일러를 포함한다.

### Task 2: 인프라와 실제 RBAC 이벤트 수신 관문

**Files:**
- Create: `infra\main.bicep`, `infra\workload.bicep`, `infra\eventing.bicep`, `README.md`
- Modify: `host.json`의 배포 환경 연동에 필요한 부분만

**Interfaces:**
- 사용: 작업 1의 Azure 함수 이름 `RbacAssignmentObserver`와 `EXPECTED_SUBSCRIPTION_ID`.
- 입력: `resourceGroupName: string`, `location: string`, `namePrefix: string`, `enableEventSubscription: bool = false`.
- 출력: `functionAppName`, `functionAppResourceId`, `managedIdentityPrincipalId`, `applicationInsightsName`, `workspaceResourceId`, `systemTopicName` 문자열.
- 이벤트 연결: `enableEventSubscription=false`는 실행 리소스만, `true`는 코드 배포 후 Event Grid 연결까지 수행한다. 재배포로 다른 리소스를 삭제하지 않는다.

- [ ] **Step 1: 소스 검증 절차와 배포 전 확인 목록을 README에 작성한다.**

실제 기대 결과는 새 테스트 Reader 할당의 ID에 대응하는 `RbacRoleAssignmentReceived` 로그이다. 테스트할 구독, 테넌트, 지역, 사용자 Object ID, 범위와 비용 승인을 먼저 받는다. 기존 동일 Reader 할당이 있으면 삭제하지 말고 별도 테스트 범위를 선택한다.

- [ ] **Step 2: Bicep 파일이 없어 정적 검증이 실패함을 확인한다.**

실행: `az bicep build --file .\infra\main.bicep`

기대: 파일 없음으로 실패. Azure CLI가 없다면 도구 미설치 실패로 구분하고 먼저 필요한 도구 설치 승인을 확인한다.

- [ ] **Step 3: 구독 범위 Bicep과 필요한 배포 안내를 작성한다.**

`main.bicep`은 전용 리소스 그룹과 workload 모듈, Function 관리 ID의 구독 Reader 역할, 조건부 eventing 모듈을 정의한다. 이름은 prefix와 구독·리소스 그룹의 안정적인 고유값에서 파생한다. 기본 호스팅은 Linux Flex Consumption `FC1`, Python `3.11`이다.

`workload.bicep`은 공식 Flex 관리 ID 예제에 따라 Storage 호스트·배포 접근에 필요한 역할과 인증 설정을 완성한다. ARM/Graph용 시스템 할당 ID를 유지하고 순환 의존성을 피한다. Storage 자격 증명이나 Function 키는 output하지 않는다. 기본 Application Insights 연동과 `EXPECTED_SUBSCRIPTION_ID`를 설정한다.

`eventing.bicep`은 시스템 토픽의 source를 현재 구독 ID, topicType을 `Microsoft.Resources.Subscriptions`로 지정한다. 이벤트 구독의 목적지는 Function의 전체 리소스 ID와 `/functions/RbacAssignmentObserver`이다. 설계서 6절의 이벤트 유형·키·연산자·값을 그대로 사용하고, 하위 scope를 제외하는 subject 접두사는 설정하지 않는다.

- [ ] **Step 4: 정적 검증과 배포 전 변경 내용을 확인한다.**

실행: `az bicep build --file .\infra\main.bicep`

기대: 오류 없이 컴파일된다. 사용자가 지정한 `$location`, `$resourceGroupName`, `$prefix`로 다음 what-if를 수행하고 생성·역할 부여 범위가 승인된 대상뿐인지 확인한다.

```powershell
az deployment sub what-if --location $location --template-file .\infra\main.bicep --parameters resourceGroupName=$resourceGroupName location=$location namePrefix=$prefix enableEventSubscription=false
```

지역은 `az functionapp list-flexconsumption-locations -o table`로 확인한다. 미지원이면 임의로 지역이나 요금제를 바꾸지 않는다.

- [ ] **Step 5: 승인된 리소스 배포 후 수신 코드를 게시하고 이벤트를 연결한다.**

what-if와 같은 매개변수로 `az deployment sub create`를 수행한다. 출력된 앱 이름으로 `func azure functionapp publish $appName`을 실행하고 `az functionapp function list --resource-group $resourceGroupName --name $appName -o table`에서 함수 등록을 확인한다. 코드 게시 후 `enableEventSubscription=true`로 다시 배포한다. 배포 도구가 없으면 그 실패를 근거로 설치를 안내한다.

- [ ] **Step 6: 실제 Reader 할당으로 이벤트 수신을 검증한다.**

승인된 사용자와 범위에 GUID 이름의 임시 Reader 할당을 만든다. 생성에 성공한 할당 ID만 기록한다. Activity Log의 작업 성공, Event Grid 전달 지표, Application Insights의 수신 eventId·할당 ID를 대조한다. 토큰이나 전체 claims를 로그에 추가하지 않고 필요한 원본 필드는 디버거 또는 제한된 임시 진단으로 확인한다.

관측되지 않으면 필터와 배포 및 전달 상태를 조사하고 차단 상태로 멈춘다. 관측 후 생성한 임시 할당만 ID로 제거한다. 명령 및 결과의 개인·환경 식별 정보는 공개 커밋에 넣지 않는다.

- [ ] **Step 7: 인프라와 수신 검증 안내를 커밋한다.**

정적 검증과 실제 수신 검증을 구분해 결과를 보고한다. 커밋 메시지: `feat: provision subscription RBAC event pipeline`. 실제 수신이 미검증이면 다음 작업으로 넘어가지 않는다.

### Task 3: 관리 ID 기반 ARM·Graph 읽기 클라이언트

**Files:**
- Create: `rbac_monitor\azure_client.py`, `scripts\Grant-GraphUserRead.ps1`
- Modify: `tests\fixtures.py`, `README.md`
- Test: `tests\test_azure_client.py`

**Interfaces:**
- 생성자: `AzureLookupClient(credential: TokenCredential, session: requests.Session, *, sleep: Callable[[float], None] = time.sleep)`.
- 메서드: `get_role_assignment(role_assignment_id: str) -> RoleAssignment`, `get_role_name(role_definition_id: str) -> str`, `get_user(principal_id: str) -> UserProfile`.
- `RoleAssignment`: `principal_id: str`, `principal_type: str`, `role_definition_id: str`, `scope: str`.
- `UserProfile`: `user_id: str`, `display_name: str | None`, `user_principal_name: str | None`, `company_name: str | None`.
- 오류: `LookupFailure(RuntimeError)`에 `stage: str`, `code: str`, `http_status: int | None`, `request_id: str | None`. stage는 `roleAssignment`, `roleDefinition`, `user`.
- 관리자 스크립트: `Grant-GraphUserRead.ps1 -TenantId <guid> -ManagedIdentityObjectId <guid> [-WhatIf]`. 다른 권한을 묶어 부여하지 않는다.

- [ ] **Step 1: API 계약과 실패 분류 테스트를 먼저 작성한다.**

`test_gets_assignment_then_role_definition`은 ARM 응답 필드와 역할명을 확인한다. `test_user_query_explicitly_selects_company_name`은 Graph URL과 `$select=id,displayName,userPrincipalName,companyName` 및 반환값을 검증한다. HTTP 응답은 `http_response`와 mock Session으로 제공하며 실제 네트워크는 사용하지 않는다.

```python
def test_company_name_null_is_preserved(self):
    self.session.get.return_value = http_response(
        200, {"id": PRINCIPAL_ID, "displayName": "Test User",
              "userPrincipalName": "test@example.invalid", "companyName": None}
    )
    self.assertIsNone(self.client.get_user(PRINCIPAL_ID).company_name)

def test_forbidden_is_not_retried_or_converted_to_empty_user(self):
    self.session.get.return_value = http_response(403, {})
    with self.assertRaises(LookupFailure) as raised:
        self.client.get_user(PRINCIPAL_ID)
    self.assertEqual(raised.exception.http_status, 403)
    self.assertEqual(self.session.get.call_count, 1)
```

추가 테스트는 빈 문자열 보존, companyName 키 누락의 `invalidResponse`, 사용자 404의 실패, 역할 할당 404 후 정상 응답의 재시도, 429 후 정상 응답, 긴 Retry-After의 즉시 실패, 타임아웃 재시도 소진, 잘못된 JSON 및 3xx 응답 거부이다. 각 API가 올바른 토큰 audience와 고정 호스트를 사용하고 redirect를 따르지 않는지도 확인한다.

- [ ] **Step 2: 테스트가 클라이언트 구현 부재로 실패함을 확인한다.**

실행: `python -m unittest tests.test_azure_client -v`

기대: 모듈 또는 클래스가 없어 실패한다.

- [ ] **Step 3: 읽기 전용 조회와 유한 재시도를 구현한다.**

ARM 호스트는 `https://management.azure.com`, scope는 `https://management.azure.com/.default`이다. 역할 할당 API 버전은 `2022-04-01`이다. 역할 정의는 ARM이 반환한 유효한 역할 정의 경로로 조회한다. Graph 호스트는 `https://graph.microsoft.com/v1.0`, scope는 `https://graph.microsoft.com/.default`이며 대상은 GUID `principalId`이다.

Session GET에 `timeout=(3.05, 10)`과 `allow_redirects=False`를 사용한다. API 호출은 최대 3회, 기본 재시도 대기는 1초·2초이다. 429의 Retry-After는 초 및 HTTP 날짜 형식을 처리하고, 10초를 넘는 대기가 필요하면 `retryAfterExceedsBudget` 오류로 중단한다. 역할 할당 조회의 404에만 전파 지연 재시도를 적용한다. 자격 증명 취득 실패, HTTP 오류, 응답 계약 위반을 안전한 `LookupFailure`로 변환하고 원본 본문을 오류 문자열에 넣지 않는다.

- [ ] **Step 4: 관리자 전용 Graph 권한 설정 스크립트를 작성한다.**

현재 Azure CLI 로그인 테넌트가 `TenantId`와 일치하는지 검사한다. Graph 서비스 주체는 앱 ID `00000003-0000-0000-c000-000000000000`로 찾고, 활성화된 Application 유형 `User.Read.All` 앱 역할을 선택한다. 관리 ID의 기존 앱 역할 부여를 확인한 뒤 없을 때만 해당 관리 ID의 `appRoleAssignments`에 추가한다. `SupportsShouldProcess`와 `-WhatIf`를 지원하고 자동 권한 상승이나 관리자 로그인 유도는 하지 않는다.

권한 변경의 토큰 캐시·전파 지연, 관리자 단계의 필요성과 Reader와의 차이를 README에 기록한다. 스크립트는 Bicep 배포에서 자동 실행하지 않는다.

- [ ] **Step 5: 클라이언트 테스트와 관리자 스크립트 구문을 검증한다.**

실행: `python -m unittest tests.test_azure_client -v`

기대: 모든 API 테스트가 실제 통신 없이 PASS. PowerShell Parser의 `ParseFile`로 스크립트를 검사해 구문 오류가 0개인지 확인한다. 관리자 승인을 받은 환경에서 `-WhatIf`가 역할 부여를 수행하지 않는지 확인하고, 실제 권한 부여는 별도 승인 후 실행한다.

- [ ] **Step 6: 클라이언트와 관리자 설정 안내를 커밋한다.**

커밋 메시지: `feat: add managed identity RBAC and user lookups`.

### Task 4: 결과 로그 연결과 종단 간 검증

**Files:**
- Create: `rbac_monitor\processor.py`, `tests\test_processor.py`, `docs\queries.kql`
- Modify: `function_app.py`, `tests\test_function_app.py`, `README.md`

**Interfaces:**
- 사용: 작업 1의 `RoleAssignmentEvent`, 작업 3의 `AzureLookupClient`, 응답 dataclass와 `LookupFailure`.
- 출력: `build_observation(event: RoleAssignmentEvent, client: AzureLookupClient, *, observed_at: datetime) -> dict[str, object]`.
- 출력: `get_client() -> AzureLookupClient`는 시스템 ID의 `ManagedIdentityCredential()`와 재사용 Session으로 클라이언트를 준비한다. `handle_role_assignment`의 시그니처는 유지한다.
- 로그 필드와 상태값은 설계서 9절을 그대로 구현한다. 진단 수신 로그와 완료 결과 로그를 구별한다.

- [ ] **Step 1: 보강 결과와 실제 함수 로그의 실패 테스트를 작성한다.**

`create_autospec(AzureLookupClient, instance=True)`로 클라이언트를 주입한다. 고정 UTC 시각으로 `observedAt`을 검증한다. 성공 사용자는 `enrichmentStatus=succeeded`, null 또는 빈 문자열은 원래 값을 보존하며 `companyNameNotSet`, 비사용자는 Graph 호출 없이 `notApplicable`이어야 한다.

```python
def test_group_does_not_expand_members_or_read_user(self):
    self.client.get_role_assignment.return_value = RoleAssignment(
        PRINCIPAL_ID, "Group", ROLE_DEFINITION_ID, self.scope
    )
    record = build_observation(self.event, self.client, observed_at=self.now)
    self.assertEqual(record["enrichmentStatus"], "notApplicable")
    self.assertIsNone(record["companyName"])
    self.client.get_user.assert_not_called()
```

`test_recipient_comes_from_assignment_not_actor_claims`는 ARM 대상과 다른 actor를 가진 SDK 이벤트를 진입 함수에 넣고 ARM의 principalId로 Graph를 호출했는지 확인한다. `test_failure_logs_only_safe_metadata_and_reraises`는 403 오류에 실패 eventName과 HTTP 상태가 있지만 본문·토큰·전체 claims 및 성공 로그가 없음을 확인한다.

`test_duplicate_and_out_of_order_events_remain_correlatable`은 같은 이벤트를 두 번, 오래된 이벤트를 나중에 처리해 각각의 eventId·eventTime을 보존하고 상태 저장이나 순서 가정을 하지 않음을 검증한다. 잘못된 소스와 작업명은 lookup 호출 전에 거부되어야 한다.

- [ ] **Step 2: 신규 테스트가 아직 연결되지 않은 결과 처리 때문에 실패함을 확인한다.**

실행: `python -m unittest tests.test_processor tests.test_function_app -v`

기대: `build_observation` 부재 또는 관측 결과 로그 부재로 실패한다.

- [ ] **Step 3: 관측 결과와 진입 함수의 안전한 오류 처리를 연결한다.**

`build_observation`은 역할 할당 → 역할 정의 → 사용자 순서로 조회하고 설계서의 `schemaVersion=1`, `eventName=RbacRoleAssignmentObserved` 및 모든 계약 필드를 반환한다. `User`가 아닌 유형은 그대로 기록하며 사용자 식별 필드는 null로 둔다.

진입 함수는 파싱 성공 후에만 클라이언트를 준비한다. UTC 조회 시각을 전달하고 표준 로거에 JSON 한 줄을 기록한다. `EventValidationError`와 `LookupFailure`는 `RbacRoleAssignmentEnrichmentFailed`로 안전한 메타데이터만 기록한 뒤 다시 발생시킨다. 환경 설정 누락은 명시적인 구성 오류로 실패시킨다. 포괄적인 예외 삼키기나 빈 결과 반환을 추가하지 않는다.

- [ ] **Step 4: KQL과 한국어 운영 안내를 완성한다.**

Application Insights에서는 `traces.message`, Workspace에서는 `AppTraces.Message`를 JSON으로 해석한다. 사용자·역할·scope·companyName과 상태를 보여 주고, 성공 결과의 중복은 eventId로 정리하며 실패는 별도 쿼리로 보여 준다. 기록에는 `cloud_RoleName` 또는 `AppRoleName` 필터를 적용해 다른 Function 로그와 섞지 않는다.

README에는 Graph 관리자 부여와 전파 확인, 사용자 null·게스트·그룹 의미, 샘플링 및 수집 한도, 전달 실패와 함수 실행 실패의 차이, PIM·관리 그룹·삭제 제외, 감사 원장 아님을 명시한다. 정리는 실제 생성한 테스트 할당 ID만 삭제하는 절차와 전용 리소스 그룹의 리소스별 확인 후 삭제 안내로 제공한다. 관리 ID의 Graph 앱 역할을 명시적으로 해제하는 방법도 기록한다.

- [ ] **Step 5: 전체 로컬 테스트와 인프라 정적 검증을 수행한다.**

```powershell
python -m unittest discover -s .\tests -p "test_*.py" -v
az bicep build --file .\infra\main.bicep
git --no-pager diff --check
```

기대: 단위 테스트 모두 PASS, Bicep 오류 없음, 공백 오류 없음. 작업 1과 3 이후 코드가 바뀌었으므로 관련 테스트를 다시 실행한다.

- [ ] **Step 6: 최종 코드를 게시하고 실제 결과를 대조한다.**

승인된 Function에 게시하고 함수 등록 및 런타임 오류 유무를 확인한다. Graph 앱 역할이 준비된 상태에서 승인된 사용자와 scope에 새 임시 Reader 할당을 만든다. 삭제 전에 해당 할당 ID의 관측 로그에서 principalId, roleName, scope와 Graph의 companyName이 일치하는지 확인한다. 사용자 속성은 변경하지 않는다.

조회 시각과 이벤트 시각을 구분하고, 가능하면 속성 미설정 사용자와 비사용자 직접 할당도 승인된 대상으로 확인한다. 무관한 리소스 쓰기가 완료 관측 로그를 만들지 않는지도 확인한다. 미검증 scope와 시나리오는 남은 검증으로 보고한다. 테스트용 할당은 생성 성공한 ID만 제거한다.

- [ ] **Step 7: 완료 결과와 직접 연결된 파일을 커밋한다.**

커밋 메시지: `feat: log RBAC assignment recipients and company names`. 실제 수행한 로컬 검사, Azure 검사, 차단된 검사 및 손실·중복 제약을 구분해 보고한다. 실환경 식별자와 로그 원문은 커밋하지 않는다.

## 구현 시 참고할 공식 문서

- 설계서 13절의 Event Grid, ARM, Graph와 재시도 문서.
- [EventGridEvent의 Python SDK 계약](https://learn.microsoft.com/en-us/python/api/azure-functions/azure.functions.eventgridevent?view=azure-python)
- [Flex 관리 ID Bicep 예제](https://learn.microsoft.com/en-us/azure/azure-functions/functions-create-first-function-bicep)
- [Flex 지원 지역 및 생성](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to)
- [관리 ID에 애플리케이션 앱 역할 부여](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/assign-app-role-managed-identity-powershell)

## 계획 자체 검토 결과

- 설계의 목표·비목표·접근 방식은 Global Constraints와 작업 1~4에 대응한다.
- 실제 이벤트 지원 확인은 작업 2의 필수 관문이며, 로컬 테스트와 구분했다.
- 입력·출력 타입과 메서드 이름은 Interfaces 블록 간 동일하다.
- Review Focus의 다섯 조건을 각각 소유 작업의 테스트에 반영했다.
- 실제 리소스 생성, 관리자 권한 부여와 테스트 할당은 별도 승인 전 수행하지 않는다.
- 이 문서는 실행 지침이며 구현이나 검증 완료 기록이 아니다.
