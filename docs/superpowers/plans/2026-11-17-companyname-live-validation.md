# companyName 보강 및 실제 검증 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 새 테스트 사용자의 `companyName=company001`을 실제 RBAC 이벤트의 Function 보강 로그에서 검증하고 이번에 생성한 사용자·권한·Azure 자원을 정리한다.

**Architecture:** 기존 수신 전용 Function에 선택형 ARM/Graph 보강을 추가한다. 신규 사용자와 테스트 할당은 실행자와 분리된 상태로 관리하며, 성공 요청과 연결된 보강 로그를 확인한 뒤 정리한다. 기존 자기 계정 실습과 전역 로그인 설정은 변경하지 않는다.

**Tech Stack:** Python 3.11, Azure Functions Python v2, azure-identity, azure-core, requests, unittest, Bicep, Azure CLI, Microsoft Graph v1.0.

**Spec:** [기존 승인 설계와 14절 추가 승인 범위](../specs/2026-11-15-subscription-rbac-companyname-design.md)

## Global Constraints

- `companyName=company001`, 새 사용자 1명, `accountEnabled=false`, 무라이선스, 전용 RG Reader 한 건.
- `enableIdentityEnrichment=false`가 기본값이며 기초 워크숍의 `assignSubscriptionReader=false`를 유지한다.
- Function 설정은 `ENABLE_IDENTITY_ENRICHMENT`이며 누락은 `false`, 명시적 값은 `true` 또는 `false`만 허용한다. 잘못된 값은 오류로 표면화한다.
- 성공 로그는 `RbacRoleAssignmentObserved`, 상태는 `succeeded`, `companyNameNotSet`, `notApplicable`이다.
- ARM 역할 API는 `2022-04-01`; Graph 사용자는 `$select=id,displayName,userPrincipalName,companyName`을 사용한다.
- Function은 관리 ID로 읽기만 수행하며, Graph 쓰기는 현재 계정의 명시적인 관리자 준비 단계에서만 수행한다.
- 현재 계정에 필요한 Entra 권한이 확인되기 전에는 비용이 발생하는 신규 Azure 자원을 배포하지 않는다. PIM 활성화·역할 승격·전역 로그인 변경은 하지 않는다.
- 기존 `userObjectId`는 실행자이다. 기초 상태 스키마와 자기 계정 제한을 바꾸거나 새 사용자 ID로 덮어쓰지 않는다.
- 신규 테스트 Reader는 보강 로그를 확인할 때까지 유지하고, 모든 종료 경로에서 기록된 소유 항목만 정리한다.
- 토큰·비밀번호·전체 claims·원본 Graph 응답을 출력하거나 저장하지 않는다. 실제 증거는 비공개이며 사용자 삭제는 일반 삭제이다.
- 기존 의존성으로 구현한다. 의존성 누락이 확인되지 않으면 재설치하지 않는다.
- 한국어 설계·계획·워크숍 문서, Windows 실행 경로의 백슬래시, Cloud Shell Bash 명령의 기존 표기를 유지한다.

## Review Focus

- 역할 할당이 Function 처리 전에 사라지는 경합: 고급 실험은 보강 성공 또는 제한 시간까지 대상을 유지한다(Task 3).
- Graph의 빈 회사명과 권한 오류: null·빈 문자열만 미설정이며 403은 실패 로그와 호출 실패이다(Task 1, 2).
- 상태 파일의 실행자·새 사용자 혼동: 실행자 ID 불변, 별도 신규 사용자만 생성·할당·삭제한다(Task 3).
- 생성 응답 유실과 부분 실패: 쓰기 의도를 먼저 저장하고, 소유권을 확인할 수 없는 객체를 임의로 인수·삭제하지 않는다(Task 3).
- 비슷한 로그의 오인: 받은 로그, 다른 사용자·회사명·scope, 실패 요청, 다른 InvocationId는 검증 성공이 아니다(Task 3).

## 파일 구조

- `rbac_monitor\azure_client.py`: 관리 ID 기반 ARM/Graph 읽기, 응답 검사, 제한된 재시도와 안전한 오류.
- `rbac_monitor\processor.py`: 검증된 이벤트를 보강하고 기존 설계의 결과 객체 생성.
- `function_app.py`, `infra\main.bicep`, `infra\workload.bicep`: 선택형 보강과 설정 연결.
- `scripts\companyname.py`: 현재 계정 관리자 사전 점검·준비, 신규 사용자 실험, 전용 상태, 상관관계 조회와 정리. 기존 `scripts\workshop.py`의 소유권·저장·ARM·CLI 헬퍼를 재사용한다.
- `tests\test_azure_client.py`, `tests\test_processor.py`, `tests\test_companyname_workshop.py`: 새 동작의 집중 테스트.
- `tests\test_function_app.py`, `tests\test_workshop_artifacts.py`: 진입점과 패키징 회귀 테스트.
- `docs\05a-companyname-validation.md`: 모듈 05 이후, 정리 모듈 06 이전에 수행하는 선택 실습.
- `README.md`, `docs\05-rbac-event-validation.md`, `docs\06-cleanup-production.md`, `docs\development.md`: 선택 실습 연결, 권한·범위·증거와 정리 안내.

기초 CLI의 엄격한 상태 스키마는 유지한다. 고급 상태는 기초 상태 옆의 `<runId>-companyname.json`에 저장하며 구독·테넌트·실행자·runId를 기초 상태와 교차 검증한다. 비밀 값은 상태에 포함하지 않는다.

---

### Task 1: ARM/Graph 읽기와 관측 결과 구현

**Files:**
- Create: `rbac_monitor\azure_client.py`, `rbac_monitor\processor.py`
- Test: `tests\test_azure_client.py`, `tests\test_processor.py`

**Interfaces:**
- Consumes: `rbac_monitor.events.RoleAssignmentEvent`.
- Produces: `ApiError(stage: str, code: str, *, status_code: int | None = None, request_id: str | None = None)`; 오류 문자열에 응답 본문이나 토큰을 포함하지 않는다.
- Produces: `AzureClient(credential: TokenCredential, *, session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep)`.
- Produces: `AzureClient.get_assignment(resource_id: str) -> dict`, `get_role_definition(resource_id: str) -> dict`, `get_user(principal_id: str) -> dict`.
- Produces: `observe(event: RoleAssignmentEvent, client: AzureClient) -> dict[str, object]`.

- [ ] **Step 1: 실패할 집중 테스트 작성**

  `test_user_company_name_is_observed`는 대상 ID가 이벤트 수행자의 claims가 아닌 ARM `principalId`이며 회사명이 `company001`, 역할명이 `Reader`, 상태가 `succeeded`임을 검증한다. `test_missing_company_name_is_not_api_failure`는 null과 빈 문자열을 보존한다. `test_non_user_skips_graph`는 Graph 호출 없이 `notApplicable`을 반환한다.

  정상 테스트에서 `result`는 `observe`의 반환값이고 `assignment`는 모킹한 ARM 응답이다.

  ```python
  self.assertEqual(result["principalId"], assignment["properties"]["principalId"])
  self.assertEqual(result["companyName"], "company001")
  self.assertEqual(result["roleName"], "Reader")
  self.assertEqual(result["enrichmentStatus"], "succeeded")
  ```

  클라이언트 테스트는 고정 호스트·각 토큰 audience·명시적 `$select`·타임아웃을 확인한다. 403/사용자 404는 재시도 없이 실패, 역할 할당 404 후 성공과 429 후 성공, 재시도 소진, 잘못된 JSON·필수 필드·응답 ID·scope 불일치를 검증한다. 리다이렉트를 따라 다른 호스트에 토큰을 보내지 않는지 확인한다.

- [ ] **Step 2: 실패 확인**

  실행: `python -m unittest tests.test_azure_client tests.test_processor -v`

  기대: 신규 모듈이 없어 실패하며 Azure에 호출하지 않는다.

- [ ] **Step 3: 최소 구현**

  ARM과 Graph는 고정 HTTPS 호스트만 사용하며 임의 URL을 입력받지 않는다. 응답의 ID·UUID·역할 정의·할당 scope를 검증하고 잘못된 응답을 성공 결과로 만들지 않는다. `claims`는 읽지 않는다.

  연결/읽기 타임아웃은 각각 5초/15초, HTTP 시도는 최초 포함 최대 3회이다. 네트워크 일시 오류·429·재시도 가능한 5xx와 역할 할당 404만 재시도한다. `Retry-After`의 초 또는 HTTP 날짜를 고려하되 대기가 10초를 초과하면 서버보다 이르게 재시도하지 않고 명시적인 일시 오류로 종료한다. 헤더가 없으면 1초/2초 대기한다.

  `observe`는 원래 설계 9절 필드 전체를 생성한다. 조회한 시각을 `observedAt`에 UTC로 기록한다. 실패는 안전한 단계·분류·상태·요청 ID만 가진 `ApiError`로 표면화한다.

- [ ] **Step 4: 집중 테스트 통과 확인**

  실행: Step 2와 동일. 기대: 전체 통과, 실제 네트워크 호출 없음.

- [ ] **Step 5: 변경 파일만 커밋**

  메시지: `feat: add ARM and Graph role assignment enrichment`. 두 필수 Copilot 커밋 트레일러를 포함한다.

### Task 2: 기존 Function에 선택형 보강 연결

**Files:**
- Modify: `function_app.py`, `infra\main.bicep`, `infra\workload.bicep`
- Test: `tests\test_function_app.py`, `tests\test_workshop_artifacts.py`

**Interfaces:**
- Consumes: Task 1의 `AzureClient`, `ApiError`, `observe`.
- Produces: 기존 `handle_role_assignment(event: func.EventGridEvent) -> None` 진입점과 기존 수신 로그를 유지하는 선택형 보강.
- Produces: Bicep의 `enableIdentityEnrichment bool = false` 및 Function의 `ENABLE_IDENTITY_ENRICHMENT`.

- [ ] **Step 1: 실패할 진입점 테스트 작성**

  `test_disabled_enrichment_never_constructs_cloud_client`, `test_enabled_enrichment_logs_observed_company_name`, `test_graph_forbidden_logs_failure_and_reraises`, `test_invalid_enrichment_setting_fails`를 추가한다. 기본 모드는 기존 수신 로그만 기록하고, 활성 모드는 `company001`을 포함한 관측 로그를 기록한다. 실패 시 `Observed`를 기록하지 않으며 오류와 토큰·본문을 혼합하지 않는다.

  활성 모드에서 `record`는 기존 테스트 방식으로 캡처한 마지막 로그의 JSON이다.

  ```python
  self.assertEqual(record["eventName"], "RbacRoleAssignmentObserved")
  self.assertEqual(record["companyName"], "company001")
  self.assertEqual(record["enrichmentStatus"], "succeeded")
  ```

  패키지 테스트는 두 신규 런타임 모듈은 포함하고 고급 관리자 스크립트·상태는 포함하지 않는지 확인한다.

- [ ] **Step 2: 실패 확인**

  실행: `python -m unittest tests.test_function_app tests.test_workshop_artifacts -v`

  기대: 보강 관련 새 테스트가 실패한다.

- [ ] **Step 3: 최소 연결 구현**

  검증된 이벤트의 `RbacRoleAssignmentReceived`는 유지한다. 보강 활성 시에만 `ManagedIdentityCredential`을 생성해 `observe`를 호출하고 결과를 기록한다. `ApiError`는 기존 `RbacRoleAssignmentEnrichmentFailed`로 안전하게 기록한 뒤 재전파한다. 기본 모드에는 ARM/Graph 조회나 자격 증명 초기화를 추가하지 않는다.

  Bicep 매개변수를 workload와 앱 설정에 전달한다. 기본 Reader 정책이나 기초 워크숍의 두 명시적 false 매개변수는 바꾸지 않는다. Graph 권한 부여를 Bicep에 숨기지 않는다.

- [ ] **Step 4: 테스트와 Bicep 정적 검증**

  실행: Step 2와 동일, 이후 `az bicep build --file .\infra\main.bicep --stdout`.

  기대: 테스트와 빌드 성공. 생성 ARM은 비공개 검증 위치에 보관하며 기존 추적 파일을 불필요하게 덮어쓰지 않는다.

- [ ] **Step 5: 변경 파일만 커밋**

  메시지: `feat: enable opt-in companyName observation`. 두 필수 트레일러를 포함한다.

### Task 3: 신규 사용자 전용 실험과 정리 구현

**Files:**
- Create: `scripts\companyname.py`, `tests\test_companyname_workshop.py`, `docs\05a-companyname-validation.md`
- Modify: `README.md`, `docs\05-rbac-event-validation.md`, `docs\06-cleanup-production.md`, `docs\development.md`

**Interfaces:**
- Consumes: 기존 `workshop.load_state`, `check_run`, `owned_group`, `save_json`, `scoped`, `arm_get`, `validate_role`, `delete_role`, `write_evidence`, `cleanup_run`.
- Produces: `preflight(state_file: Path) -> dict`, `prepare(state_file: Path, company_name: str) -> dict`, `exercise(state_file: Path, timeout_seconds: int = 900) -> dict`, `cleanup(state_file: Path) -> dict`.
- Produces: `python -m scripts.companyname preflight STATE`, `prepare STATE --company-name company001`, `exercise STATE --timeout-seconds 900`, `cleanup STATE`.
- Produces: `<runId>-companyname.json`과 비공개 `companyname-verification.json`, `companyname-cleanup.json`. 기초 `state["assignment"]`도 수정하지 않는다.

- [ ] **Step 1: 실패할 상태·생성·정리 테스트 작성**

  `test_reader_only_admin_preflight_blocks_writes`는 Global Reader만 있으면 명확히 실패하며 Graph 쓰기가 없는지 확인한다. 준비된 User Administrator + Privileged Role Administrator 조합과 이미 권한이 있는 Global Administrator를 허용한다. 생성·삭제·동의에 필요한 호출자 권한도 검증한다.

  `test_prepare_creates_disabled_new_user_with_company001`은 생성 JSON의 회사명·로그인 차단과 비밀번호 미출력을 확인한다. `test_operator_identity_is_never_replaced`는 기초 상태가 바뀌지 않는지 확인한다.

  `request_body`는 가짜 Graph 전송 계층에 전달된 사용자 생성 JSON이며, `before`와 `after`는 `prepare` 전후의 기초 상태이다.

  ```python
  self.assertEqual(request_body["companyName"], "company001")
  self.assertIs(request_body["accountEnabled"], False)
  self.assertEqual(after, before)
  ```

  `test_existing_user_is_not_adopted`, `test_foreign_state_is_rejected`, `test_response_loss_preserves_reconciliation_identity`는 기존 사용자 및 타 실행 객체 보호와 응답 유실 후 재조정 대상을 검증한다.

  `test_assignment_survives_until_observed_success`는 로그 대기 중 Reader와 사용자가 유지되는지 확인한다. `test_timeout_and_interrupt_cleanup_owned_objects`와 `test_partial_cleanup_reports_remaining_objects`는 시간 초과·중단·개별 삭제 실패에서도 나머지 정리를 시도하고 잔여 항목을 보고하는지 확인한다. 재정리와 기존 Graph 권한 보존도 검증한다.

- [ ] **Step 2: 실패 확인**

  실행: `python -m unittest tests.test_companyname_workshop -v`

  기대: 신규 모듈이 없어 실패한다.

- [ ] **Step 3: 관리자 준비 및 전용 상태 구현**

  `preflight`는 현재 계정·테넌트, 지원 관리자 역할, 필요한 API 호출 권한과 검증된 관리형 도메인을 읽기 전용으로 점검한다. 부족하면 실패하며 역할 승격이나 로그인을 수행하지 않는다. 사전 점검 성공을 실제 쓰기 성공으로 간주하지 않는다.

  `prepare`는 새 실행의 Function 관리 ID를 검증하고 Microsoft Graph 서비스 주체에서 활성화된 Application용 `User.Read.All` 역할을 찾는다. 회사명은 공백만으로 이루어지지 않은 1~64문자로 검사하고 값을 임의로 변형하지 않는다. 새 앱 역할의 정확한 tuple과 사용자 고유 이름을 쓰기 전에 저장한다. 새 사용자는 `rbac-companyname-<runId>` 이름과 검증된 도메인으로 생성한다. 비밀번호는 메모리에서만 생성해 TLS 요청으로 보내고 상태·명령행·예외에 넣지 않는다.

  Graph 요청은 고정 호스트, 타임아웃, 리다이렉트 금지와 명시적 상태 검사를 사용한다. POST를 무작정 재시도하지 않는다. 부분 실패 및 응답 유실은 기록된 고유 대상만 재조회하며 소유권이 모호하면 자동 인수·삭제하지 않는다.

- [ ] **Step 4: 실패할 로그 상관관계 테스트 작성**

  `test_received_log_is_not_enrichment_proof`, `test_wrong_company_principal_role_or_scope_never_verifies`, `test_failed_or_mismatched_invocation_never_verifies`를 추가한다. 올바른 `Observed`·Activity 쓰기 성공·같은 성공 요청만 허용한다.

  각 불일치 사례는 가짜 조회 응답으로 주입하며, 가짜 시간으로 제한 시간에 도달시켜 다음을 검증한다.

  ```python
  with self.assertRaises(workshop.WorkshopError):
      exercise(state_file, timeout_seconds=1)
  ```

- [ ] **Step 5: 실패 확인 후 실험·검증·정리 구현**

  Step 2 명령으로 새 테스트의 실패를 확인한다. 이후 `exercise`에서 신규 사용자의 RG Reader 한 건을 만들고 Activity 쓰기 성공과 `Observed`를 제한 시간 동안 조회한다. 할당 ID, 사용자 ID, 역할 정의와 `Reader`, RG scope, `company001`, `succeeded`를 비교한다. 요청의 `OperationId`와 `InvocationId`, `Success=true`도 일치해야 한다.

  검증 증거를 저장한 뒤 `finally`에서 고급 `cleanup`을 호출한다. 정리는 신규 테스트 Reader → 이번에 만든 Graph 앱 역할 → 새 사용자 순서이며 개별 실패를 명시적으로 모아 잔여 항목과 함께 실패로 보고한다. 삭제 후에는 각각 다시 조회한다. `cleanup` 명령은 준비 단계 실패 후에도 재실행할 수 있어야 한다.

  기존 `workshop.cleanup_run`은 별도 확인 문구로 Function 구독 Reader와 RG를 정리한다. 고급 정리가 실패한 상태에서 RG부터 삭제해 관리 ID의 증거를 잃지 않도록 문서에 순서를 명시한다.

- [ ] **Step 6: 집중 테스트·선택 실습 문서 검증**

  실행: `python -m unittest tests.test_companyname_workshop tests.test_workshop_state tests.test_workshop_lifecycle tests.test_workshop_docs -v`

  기대: 통과. README는 기초 과정 시간·권한을 유지하며 선택 실습을 별도 표시한다. 문서는 관리자 사전 점검, 코드 게시와 권한 준비 후 활성화, 모듈 06 전 수행, 비공개 증거, 실패 재정리 절차를 설명한다.

- [ ] **Step 7: 변경 파일만 커밋**

  메시지: `feat: add isolated companyName workshop experiment`. 두 필수 트레일러를 포함한다.

### Task 4: 전체 회귀 검사와 실제 검증·부재 확인

**Files:**
- Modify: `docs\05a-companyname-validation.md`, `docs\development.md`
- Evidence: 세션 비공개 파일 디렉터리와 무시된 실행 상태. 증거 원문은 커밋하지 않는다.

**Interfaces:**
- Consumes: Tasks 1~3, 기존 `scripts\workshop.py` 배포 상태·패키징·정리 명령, 기존 Bicep 및 게시 절차.
- Produces: 실제 회사명 검증 여부, 시간별 이벤트 흐름, 독립적인 정리 확인과 정확한 미검증 범위.

- [ ] **Step 1: 전체 검증**

  실행: `python -m unittest discover -s .\tests -p "test_*.py" -v`, `git --no-pager diff --check`, Bicep 빌드.

  기대: 전부 통과. 코드 변경 뒤에는 관련 검증을 다시 수행한다. 문서만 바뀌면 문서 테스트로 한정한다.

- [ ] **Step 2: Native 방식 전체 변경 리뷰**

  구현은 현재 세션에서 직접 수행한다. 새 리뷰어 한 명이 전체 구현 diff와 설계를 검토하며, 코드 실행·Entra 생성·권한 부여·정리는 맡기지 않는다. 실환경 실행 전에 의미 있는 지적을 수정하고 관련 테스트를 다시 통과시킨다.

- [ ] **Step 3: 현재 계정의 권한 준비 재확인**

  새 기초 실행 상태를 만들고 고급 `preflight`를 실행한다. 관리자에게 준비를 요청한 것을 활성화 완료로 간주하지 않는다. 역할·호출 권한이 부족하면 Azure 비용 자원을 만들지 않고 실제 검증을 차단 상태로 보고한다.

- [ ] **Step 4: 승인된 구독에만 임시 배포**

  지역은 기존 `koreacentral`, 전용 RG는 새 실행 상태가 생성한 이름을 사용한다. 기록된 소유권 태그로 새 RG를 만든 뒤 validate/what-if를 수행하고 비용 자원을 배포한다. 첫 배포는 `createResourceGroup=false`, `assignSubscriptionReader=true`, `enableIdentityEnrichment=false`, `enableEventSubscription=false`이다.

  배포 성공 및 출력과 관리 ID를 확인한다. 기존 패키징과 Linux 원격 빌드 게시 절차를 사용한다. `prepare --company-name company001`을 실행한 뒤 보강과 이벤트 연결을 활성화한다. `--no-wait` 제출만으로 배포 성공을 판정하지 않는다.

- [ ] **Step 5: 실제 이벤트 검증**

  `exercise`를 실행하고 단 하나의 신규 Reader 할당으로 성공을 기다린다. 실제 Function 관리 ID가 조회한 `company001`과 성공 요청의 상관관계가 필수이다. 부족하면 수신 성공이나 운영자 Graph 조회를 대체 증거로 사용하지 않는다.

  실패 시 안전한 오류·전달 지표를 조사하고 필요한 변경을 검증한 뒤 재시도 여부를 판단한다. 수집 지연만으로 새 역할 할당을 반복 생성하지 않는다. 이벤트 전달 지표에 수치 표본이 없으면 0으로 기록하지 않는다.

  소유권 태그를 보존하면서 전용 RG 태그 갱신을 비-RBAC 대조 작업으로 한 번 수행한다. Activity Log의 대조 작업 성공과 관측창 내 해당 작업의 보강 로그 부재를 기록하며, 제한된 관측을 무기한 전달 보장으로 해석하지 않는다.

- [ ] **Step 6: 전체 정리 및 독립 재조회**

  고급 정리의 완료를 확인하고 기존 dry-run·정확한 확인 문구로 구독 Reader와 RG를 제거한다. 소유한 구독 배포 기록도 목록을 확인해 이번 실행 기록만 정리한다.

  사용자 활성 객체, Graph 앱 역할, 테스트 Reader, Function 구독 Reader, RG를 새 조회로 확인한다. 삭제된 사용자 객체의 영구 삭제는 하지 않는다. 권한 부족·조회 실패를 부재로 처리하지 않으며 잔여 항목이 있으면 완료로 보고하지 않는다.

- [ ] **Step 7: 검증된 결과만 문서화하고 커밋**

  실제 수행 시각, 할당 성공 → Function 관측 → 로그 수집의 지연과 `company001` 대조, 정리 결과를 비공개 보고서에 저장한다. 공개 문서는 ID를 익명화하고 기초 수신 실측과 회사명 보강 실측을 구분한다. 실제 검증이 막혔다면 사유와 미검증 상태를 명시한다.

  문서 테스트와 `git --no-pager diff --check` 후 `docs: record companyName validation results`로 커밋한다. 필수 트레일러를 포함하며 공유 로그인 설정이나 원격 기록을 강제로 변경하지 않는다.

## 실행 전 검토

- 기존 승인 설계 1~13절은 Task 1~2의 런타임 계약으로 유지하며, 14절은 Task 3~4의 신규 사용자·안전한 실험 순서로 구현한다.
- 계획 검토가 끝나기 전에는 구현을 시작하지 않는다. 현재 계정의 필요한 관리자 역할 준비는 실환경 검증의 선행 조건이며, 권한 준비가 확인되기 전에는 비용 자원을 배포하지 않는다. 로컬 구현·검증은 계획 승인 후 먼저 진행할 수 있다.
- 실행 방식은 앞서 선택한 Native를 유지한다. 전체 로컬 회귀 및 새 리뷰어의 검토 후 실제 Azure 검증을 수행한다.
